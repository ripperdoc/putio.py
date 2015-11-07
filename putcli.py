#!/usr/bin/env python
import putio, os.path, re, click, logging
from collections import defaultdict

'''
TODO
- Make progressbar show status of resumed download (e.g. not start at 0)
- Allow multiple dl patterns from command line
- Optionallt apply metadata to the download paths (e.g. sort by metadata)
'''

OAUTH_TOKEN = "J85Q6PEI"

logger = logging.getLogger(putio.__name__)
logging.basicConfig(level=logging.INFO)
logging.getLogger("requests").setLevel(logging.WARNING)

#Metadata patterns
# TODO use this library instead https://github.com/wackou/guessit
metadata_patterns = {
	'year' : '(19|20)\d\d',
	'resolution' : '480p|720p|1080p',
	'source' : 'BRRip|BluRay|Blu-ray|BDRip|DVDRip|HDRip|HDTV|DVDSCR|WEB',
	'audio' : 'DTS|AC3|AAC|DD5.1',
	'video' : 'h264|x264|Xvid|h 264',
	'video_ext' : '\.avi|\.mp4|\.m4v|\.mkv|\.divx|\.mpg$',
	'episode' : '(S\d{1,3}|\d{1,3}x|ep\.)(?P<episode>\d{1,3})', #S01E02, 1x02, ep.02
	'season' : '(S|Season )(?P<season>\d{1,3})'
}
path_comp = '[^/]+'

class Pattern(object):
	def __init__(self, source, dest):
		if source.endswith("/"):
			source = source + '.+' # match subpaths by default
		if not source.startswith("/"):
			source = "/"+source
		self.source = source
		self.dest = dest
		self.source_re = re.compile(source)
		self.dest_re = re.compile(dest)

	def __str__(self):
		return '"put.io:%s" -> "%s"' % (self.source, self.dest)

	def __repr__(self):
		return "%s" % self

move_patterns = []
	# Pattern(
	# 	'/TV/.+',
	# 	'/Volumes/Videos/TV/'),
	# Pattern(
	# 	'/Movies/.+',
	# 	'/Volumes/Videos/Movies/')

metadata_re = {k: re.compile(v) for k,v in metadata_patterns.iteritems()}
title_pattern = re.compile('/([^/]+?)(( ?\W?%%\W?)+|\W?$)') #([ -()]{2,}|.?$)
space_pattern = re.compile('[._]')

@click.command()
@click.argument('src', nargs=1, required=True)
@click.argument('dst', nargs=1, required=True,
	type=click.Path(exists=True, file_okay=False, writable=True))
@click.option('--dry-run', default=False, is_flag=True,
	help="Don't actually download, just print what would happen")
@click.option('--delete-after', default=False, is_flag=True,
	help="Delete files on put.io after confirmed successful download")
@click.option('--logfile', type=click.File(mode='a'),
	help="File to log messages to")
def dl(src, dst, dry_run, delete_after, logfile):
	"""Downloads files from put.io based on SRC pattern and DST folder"""

	move_patterns.append(Pattern(src, dst))
	client = putio.Client(OAUTH_TOKEN)
	click.echo("PutCLI will %s, using first matching put.io paths: \n\t%s"
		% ( 'download' if not dry_run else 'check what to download',
			'\n\t'.join([str(i) for i in move_patterns])))
	for path, dirs, files in walk('/', client.File.get(0)):
		for d in dirs[:]: # iterate on dir copy to avoid changing while iterating
			dirpath = os.path.join(path, d.name)
			# click.echo("Testing dirpath %s from %s" % (dirpath,d))
			match = False
			for p in move_patterns:
				if p.source_re.match(dirpath):
					match = True
					break
			if match:
				dest_dir = os.path.join(p.dest, path[1:])
				# click.echo("Source path %s, source dir %s, dest path %s, dest dir %s" % (path, d.name, dest_dir, d.name))
				click.echo('Matched "put.io:%s", download to "%s"' % (dirpath, dest_dir))
				for f, dest in d._download_directory(dest_dir, delete_after_download=delete_after, iter=True):
					label = f.name
					click.echo(dest)
					if not dry_run:
						chunk_generator = f._download_file(dest, delete_after_download=delete_after, iter=True)
						with click.progressbar(chunk_generator, length=f.size/putio.CHUNK_SIZE + 1, label=label, width=0) as bar:
							for update in bar:
								pass
				dirs.remove(d)
			else:
				logger.debug("No match %s" % dirpath)

def walk(path, anchor):
	files, dirs = [], []
	for f in anchor.dir():
		if f.is_dir():
			dirs.append(f)
		else:
			files.append(f)
	logger.debug("Yielding", path, dirs, files)
	yield path, dirs, files

	for d in dirs:
		np = os.path.join(path, d.name)
		# print "Recursing into %s, with files %s" % (np, d.dir())
		for x in walk(np, d):
			yield x

def get_metadata(path):
	metadata = defaultdict(list)
	for k, p in metadata_re.iteritems():
		named = k in p.groupindex # has a named group corresponding to pattern name
		for match in p.finditer(path):
			metadata[k].append(match.group(k if named else 0))
			#replace match with '-'
			path = path[:match.start()] + "%%" + path[match.end():]
	path = space_pattern.sub(' ',path)
	logger.debug("Total path \"%s\"" % path)
	for match in title_pattern.finditer(path):
		metadata['title'].append(match.group(1))
	return metadata

# From http://stackoverflow.com/questions/14996453/python-libraries-to-calculate-human-readable-filesize-from-bytes
suffixes = ['B', 'KB', 'MB', 'GB', 'TB', 'PB']
def humansize(nbytes):
    if nbytes == 0: return '0 B'
    i = 0
    while nbytes >= 1024 and i < len(suffixes)-1:
        nbytes /= 1024.
        i += 1
    f = ('%.2f' % nbytes).rstrip('0').rstrip('.')
    return '%s %s' % (f, suffixes[i])

if __name__ == '__main__':
    dl()

"""
Renaming ideas

Movies/{title}.{video_ext} -> /data/Videos/Movies/{filename}/{title}.{video_ext} (file)
Movies/{title}/.* -> /data/Videos/Movies/{title} (dir)

TV/{show}/{season}/{episode}/.* -> TV/{show}/
TV/{show}/{episode}/.*
TV/{show}/{title}.{video_ext}

Two approaches to scanning the paths:
1) Scan from left first, stop as soon as we have enough data to fill the
destination path, then move the whole tree.
2) Scan from right first (deepest first) and move every file on it's own.
(downside: unknown files will be harder to download to right place)


We define move patterns by a source and destination pattern pair.

We use method 2 to scan the remote tree by deepest folder first. If source
pattern matches current path, download the contents of the path to the
destination.

The source pattern contains placeholders that would match the typical naming
scheme of seasons, etc.
1) If it matches, this path (and subpaths) are approved for moving
2) Path component variables in brackets {}, are interpreted literally as metadata
e.g. if we have previous knowledge of the structure of the source. If there are
no such variables, they will be inferred.

The destination pattern does only one thing:
For any given source path (and subpaths), we will move it into the destination
pattern. The pattern will be populated with the metadata.

Examples
TV/.* -> TV/
	will move all contents of TV/ into TV/, without any restructuring.
TV/.* -> TV/Season {season}/
	will move all paths in TV/ to TV/Season X, where x
	is gathered from the actual source path. If no X, we will not move.
TV/.*/{file}.{video_ext} -> TV/Season {season}/
	will move all movie files from any source path in TV, to fit directly under
	the Season folder.
TV/.* -> TV/Season {season}/{title}/

"""
