# -*- coding: utf-8 -*-
import os
import sys
import binascii
import re
import json
import logging
import webbrowser
from urllib import urlencode

import requests
import iso8601

BASE_URL = 'https://api.put.io/v2'
ACCESS_TOKEN_URL = 'https://api.put.io/v2/oauth2/access_token'
AUTHENTICATION_URL = 'https://api.put.io/v2/oauth2/authenticate'

CHUNK_SIZE = 1024 * 8

logger = logging.getLogger(__name__)


class AuthHelper(object):

    def __init__(self, client_id, client_secret, redirect_uri, type='code'):
        self.client_id = client_id
        self.client_secret = client_secret
        self.callback_url = redirect_uri
        self.type = type

    @property
    def authentication_url(self):
        """Redirect your users to here to authenticate them."""
        params = {
            'client_id': self.client_id,
            'response_type': self.type,
            'redirect_uri': self.callback_url
        }
        return AUTHENTICATION_URL + "?" + urlencode(params)

    def open_authentication_url(self):
        webbrowser.open(self.authentication_url)

    def get_access_token(self, code):
        params = {
            'client_id': self.client_id,
            'client_secret': self.client_secret,
            'grant_type': 'authorization_code',
            'redirect_uri': self.callback_url,
            'code': code
        }
        response = requests.get(ACCESS_TOKEN_URL, params=params)
        logger.debug(response)
        assert response.status_code == 200
        return response.json()['access_token']


class Client(object):

    def __init__(self, access_token):
        self.access_token = access_token
        self.session = requests.session()

        # Keep resource classes as attributes of client.
        # Pass client to resource classes so resource object
        # can use the client.
        attributes = {'client': self}
        self.File = type('File', (_File,), attributes)
        self.Transfer = type('Transfer', (_Transfer,), attributes)
        self.Account = type('Account', (_Account,), attributes)

    def request(self, path, method='GET', params=None, data=None, files=None,
                headers=None, raw=False, stream=False):
        """
        Wrapper around requests.request()

        Prepends BASE_URL to path.
        Inserts oauth_token to query params.
        Parses response as JSON and returns it.

        """
        if not params:
            params = {}

        if not headers:
            headers = {}

        # All requests must include oauth_token
        params['oauth_token'] = self.access_token

        headers['Accept'] = headers.setdefault('Accept','application/json')

        url = BASE_URL + path
        logger.debug('url: %s', url)

        response = self.session.request(
            method, url, params=params, data=data, files=files,
            headers=headers, allow_redirects=True, stream=stream)
        logger.debug('response: %s', response)
        if raw:
            return response

        logger.debug('content: %s', response.content)
        try:
            response = json.loads(response.content)
        except ValueError:
            raise Exception('Server didn\'t send valid JSON:\n%s\n%s' % (
                response, response.content))

        if response['status'] == 'ERROR':
            raise Exception(response['error_type'])

        return response


class _BaseResource(object):

    client = None

    def __init__(self, resource_dict):
        """Constructs the object from a dict."""
        # All resources must have id and name attributes
        self.id = None
        self.name = None
        self.__dict__.update(resource_dict)
        try:
            self.created_at = iso8601.parse_date(self.created_at)
        except (AttributeError, iso8601.ParseError):
            self.created_at = None

    def __str__(self):
        return self.name.encode('utf-8')

    def __repr__(self):
        # shorten name for display
        name = self.name[:17] + '...' if len(self.name) > 20 else self.name
        return '<%s id=%r, name="%r">' % (
            self.__class__.__name__, self.id, name)


class _File(_BaseResource):

    @classmethod
    def get(cls, id):
        d = cls.client.request('/files/%i' % id, method='GET')
        t = d['file']
        return cls(t)

    @classmethod
    def list(cls, parent_id=0):
        d = cls.client.request('/files/list', params={'parent_id': parent_id})
        files = d['files']
        return [cls(f) for f in files]

    @classmethod
    def upload(cls, path, name=None, parent_id=0):
        with open(path) as f:
            if name:
                files = {'file': (name, f)}
            else:
                files = {'file': f}
            d = cls.client.request('/files/upload', method='POST',
                                   data={'parent_id': parent_id}, files=files)

        f = d['file']
        return cls(f)

    def dir(self):
        """List the files under directory."""
        return self.list(parent_id=self.id)

    def is_dir(self):
        return self.content_type == 'application/x-directory'

    def download(self, dest='.', delete_after_download=False):
        if self.is_dir():
            self._download_directory(dest, delete_after_download)
        else:            
            self._download_file(dest, delete_after_download)

    def _download_directory(self, dest='.', delete_after_download=False, iter=False):
        name = self.name
        if isinstance(name, unicode):
            name = name.encode('utf-8', 'replace')

        dest = os.path.join(dest, name)
        if not os.path.exists(dest):
            os.mkdir(dest)

        # Todo, clean up once we tested the iterator behaviour better
        for sub_file in self.dir():
            if iter:
                if sub_file.is_dir():
                    for f, dest2 in sub_file._download_directory(dest, delete_after_download, iter):
                        yield f, dest2
                else: 
                    yield sub_file, dest
            else:
                sub_file.download(dest, delete_after_download)

        if delete_after_download:
            self.delete()

    def _download_file(self, dest='.', delete_after_download=False, iter=False):
        # Check file size and name
        response = self.client.request(
            '/files/%s/download' % self.id, method='HEAD', raw=True)

        filename = re.match(
            'attachment; filename=(.*)',
            response.headers['content-disposition']).groups()[0]
        # If file name has spaces, it must have quotes around.
        filename = filename.strip('"')
        filepath = os.path.join(dest, filename)
        resume_header = {}
        resume_from = 0

        if os.path.exists(filepath):
            resume_from = os.path.getsize(filepath)
            if resume_from < self.size:
                resume_header = {'Range': 'bytes=%d-' % resume_from}
            else:
                resume_from = -1 # dont download

        # Now download with resume if available
        if resume_from > -1:
            # logger.info("%s: %s" % 
                # ("Downloading" if resume_from==0 else "Resuming", filepath))

            response = self.client.request(
                '/files/%s/download' % self.id, headers=resume_header, raw=True, stream=True)
            with open(filepath, 'ab' if resume_from else 'wb') as f:
                progress = resume_from
                for chunk in response.iter_content(chunk_size=CHUNK_SIZE):
                    if chunk:  # filter out keep-alive new chunks
                        f.write(chunk)
                        f.flush()
                        progress = progress + CHUNK_SIZE
                        if iter:
                            yield progress
        else:
            logger.info("Existing file: %s" % filepath)

        # Validate by comparing CRC32
        crc = 0
        with open(filepath, 'rb') as f:
            while True:
                chunk = f.read(CHUNK_SIZE)
                if not chunk:
                    break
                crc = binascii.crc32(chunk, crc)
        # The & inverts for sign to match what put.io uses
        crc = '%08x' % (crc & 0xffffffff)
        if crc == self.crc32:
            logger.info("Downloaded file matches remote")   
            if delete_after_download:
                self.delete()  
        else:
            logger.warning("File checksums not matching: local %s remote %s" % (crc, self.crc32))


    def delete(self):
        return self.client.request('/files/delete', method='POST',
                                   data={'file_ids': str(self.id)})

    def move(self, parent_id):
        return self.client.request('/files/move', method='POST',
                                   data={'file_ids': str(self.id), 'parent_id': str(parent_id)})

    def rename(self, name):
        return self.client.request('/files/rename', method='POST',
                                   data={'file_id': str(self.id), 'name': str(name)})


class _Transfer(_BaseResource):

    @classmethod
    def list(cls):
        d = cls.client.request('/transfers/list')
        transfers = d['transfers']
        return [cls(t) for t in transfers]

    @classmethod
    def get(cls, id):
        d = cls.client.request('/transfers/%i' % id, method='GET')
        t = d['transfer']
        return cls(t)

    @classmethod
    def add_url(cls, url, parent_id=0, extract=False, callback_url=None):
        d = cls.client.request('/transfers/add', method='POST', data=dict(
            url=url, save_parent_id=parent_id, extract=extract,
            callback_url=callback_url))
        t = d['transfer']
        return cls(t)

    @classmethod
    def add_torrent(cls, path, parent_id=0, extract=False, callback_url=None):
        with open(path) as f:
            files = {'file': f}
            d = cls.client.request('/files/upload', method='POST', files=files,
                                   data=dict(save_parent_id=parent_id,
                                             extract=extract,
                                             callback_url=callback_url))
        t = d['transfer']
        return cls(t)

    @classmethod
    def clean(cls):
        return cls.client.request('/transfers/clean', method='POST')


class _Account(_BaseResource):

    @classmethod
    def info(cls):
        return cls.client.request('/account/info', method='GET')

    @classmethod
    def settings(cls):
        return cls.client.request('/account/settings', method='GET')
