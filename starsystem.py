#!/usr/bin/env python

import errno
import os
import requests
import requests.exceptions as rex
import sys
import tempfile
import time
from contextlib import contextmanager
from requests.packages.urllib3.exceptions import InsecureRequestWarning
from twitter.common import app, log


class RequestError(Exception):
    pass


class SubsonicError(Exception):
    pass


API_VERSION = '1.14.0'

def configure_app(app):
    """ Register the application's options, set usage, and configure submodules. """

    app.set_usage("{} [opts]\nOptions marked with * are required.".format(app.name()))

    app.add_option('-i', '--uri', dest='subsonic_uri',
                   help='* URI of the Subsonic server.')
    app.add_option('-u', '--user', dest='username',
                   help='* Username on the specified Subsonic server.')
    app.add_option('-t', '--token', dest='token',
                   help='* API token for the given username/salt combination\n'
                        'See: http://www.subsonic.org/pages/api.jsp')
    app.add_option('-s', '--salt', dest='salt',
                   help='* Salt used to generate the API token.')
    app.add_option('-p', '--path', dest='download_path',
                   help='* Path to the directory whither songs will be downloaded.')
    app.add_option('-S', '--since', dest='since', type='date',
                   help='Collect all songs since the specified date.')
    app.add_option('-I', '--insecure', dest='insecure', default=False, action="store_true",
                   help='Don\'t verify SSL certificates. Verification is enabled by default.')

    app.set_option('twitter_common_log_disk_log_level', 'NONE', force=True)

def required_options_present(options, materialized_options):
    """ 
    Check for the presence of required options, with the side effect of
    logging missing options.
    """
    missing_options = []
    for option in sorted(options, key=lambda option: option.get_opt_string()):
        if option.dest is not None:
            materialized_opt = getattr(materialized_options, option.dest)
            if option.help.startswith('*') and materialized_opt in (None, ''):
                missing_options.append(option)
    if len(missing_options) > 0:
        for option in missing_options:
            log.error('Required option is missing: {}'.format(option.get_opt_string()))
        return False
    else:
        return True

def get_sync_file_path(download_path):
    return os.path.join(download_path, '.synced_to')

def get_start_date(download_path, starred_songs, songs_sorted=False):
    """ 
    Find the most recent starred date of synced songs in the download path.

    This will be explicitly stored in a file, but if that's missing
    it can be calculated from the directory contents.

    Return epoch time if there are no valid results otherwise.
    """
    try:
        with open(get_sync_file_path(download_path)) as sync_file:
            try:
                return time.gmtime(sync_file.readline().strip())
            except TypeError:
                # invalid file contents
                pass
    except EnvironmentError:
        pass
    starred_song_paths = { song['path'] for song in starred_songs if song.get('path') is not None }
    download_path_files = { os.path.join(os.path.relpath(path, download_path), filename) 
                            for path, _, filenames in os.walk(download_path)
                                for filename in filenames }
    synced_songs = [ song for song in starred_songs if song.get('path') in download_path_files ]
    # don't waste time if songs are already sorted
    get_most_recently_starred = (
        lambda x: x[-1] if songs_sorted else partial(max, key=song_to_starred_time_struct))
    if len(synced_songs) > 0:
        return song_to_starred_time_struct(get_most_recently_starred(synced_songs))
    else: 
        return time.gmtime(0)

def song_to_starred_time_struct(song):
    """ Take a song, return the starred date in the form of a time module 9-tuple. """
    try:
        return time.strptime(song.get('starred', ''), '%Y-%m-%dT%H:%M:%S.%fZ')
    except ValueError as e:
        return time.gmtime(0)

def create_directory_if_missing_from_path(path):
    """ Create the directories necessary to use the full path specified. """
    dirpath = os.path.dirname(path)
    # first check if it's already a directory, cheaper than try/except
    if not os.path.isdir(dirpath):
        try:
            os.makedirs(dirpath)
        except OSError as e:
            if e.errno != errno.EEXIST and not os.path.isdir(dirpath):
                # path exists and isn't a directory
                raise

def reraise_as_exception_type(cls, exception):
    """ Reraise an exception as the specified type, preserving the original traceback. """
    exception_class, _, traceback = sys.exc_info()
    msg = 'Caught exception of type {}: {}'.format(exception_class, exception)
    raise cls, cls(msg), traceback

def handle_request(f, validate_json=True):
    """ Run a function that generates a Requests.Response and handle exceptions. """
    try:
        response = f()
        response.raise_for_status()
        if validate_json:
            err = response.json()['subsonic-response'].get('error')
            if err is not None:
                raise SubsonicError('Error code {}: {}'.format(err['code'], err['message']))
        return response
    except (rex.RequestException, SubsonicError) as e:
        reraise_as_exception_type(RequestError, e)

@contextmanager
def open_tempfile_with_atomic_write_to(path, **kwargs):
    """ 
    Open a temporary file object that atomically moves to the specified
    path upon exiting the context manager.

    Supports the same function signature as `open`.

    WARNING: This is just like 'mv', it will clobber files!
    """
    _tempfile = tempfile.NamedTemporaryFile(delete=False)
    _tempfile.close()
    tempfile_path = _tempfile.name
    try:
        with open(tempfile_path, **kwargs) as file:
            yield file
            file.flush()
            os.fsync(file.fileno())
        os.rename(tempfile_path, path)
    finally:
        try:
            os.remove(tempfile_path)
        except OSError as e:
            if e.errno == errno.ENOENT:
                pass
            else:
                raise e

def main(args, options):
    if len(args) != 0:
        app.help()

    # Kind of a hack, but whatever
    option_definitions = app.Application.active()._main_options

    if not required_options_present(option_definitions, options):
        app.help()

    download_path = os.path.expanduser(options.download_path)

    base_params = dict(
        t = options.token,
        u = options.username,
        s = options.salt,
        c = app.name(),
        f = 'json',
        v = API_VERSION)

    session = requests.Session()
    session.params.update(base_params)
    session.verify = not options.insecure

    # Get starred songs
    try:
        get_starred_response = handle_request(
            lambda: session.get("https://localhost:4041/rest/getStarred.view"))
    except RequestError as e:
        log.error("Bad response from Subsonic while fetching starred songs list:\n{}".format(e))
        raise

    # Extract songs from response
    try:
        starred_songs = filter(lambda song: song['contentType'].startswith('audio'),
                               get_starred_response.json()['subsonic-response']['starred']['song'])
    except (KeyError, ValueError) as e:
        reraise_as_exception_type(RequestError, e)

    # Do nothing if no songs are starred
    if len(starred_songs) == 0:
        exit(0)

    # Sort the songs by starred date so we can sync them in chronological order
    sorted_starred_songs = sorted(starred_songs, key=song_to_starred_time_struct)
    start_date = get_start_date(download_path, sorted_starred_songs, songs_sorted=True)
    sync_file_path = get_sync_file_path(download_path)

    # Sync each song in chronological order by starred date
    for song in starred_songs:
        song_full_path = os.path.join(download_path, song['path'])
        if song_to_starred_time_struct(song) >= start_date and not os.path.exists(song_full_path):
            create_directory_if_missing_from_path(song_full_path)
            try:
                download_params = {'id': song['id']}
                download_response = handle_request(
                    lambda: session.get("https://localhost:4041/rest/download.view",
                                params=download_params),
                    validate_json=False)
            except RequestError as e:
                log.error("Failed downloading the following song: {}\n{}".format(song['path'], e))
                raise
            with open_tempfile_with_atomic_write_to(song_full_path, mode='wb') as download_file:
                download_file.write(download_response.content)
            starred_date = song_to_starred_time_struct(song)
            if starred_date != time.gmtime(0):
                try:
                    with open(sync_file_path, 'w') as sync_file:
                        sync_file.write(str(time.mktime(starred_date)))
                except EnvironmentError:
                    pass


requests.packages.urllib3.disable_warnings(InsecureRequestWarning)
configure_app(app)
app.main()
