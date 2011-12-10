#!/usr/bin/python2.7
# coding=UTF-8

# Licenses are boring
# This script may be freely used, modified and distributed as long as this notice is included
# - Matti Virkkunen <mvirkkunen@gmail.com> / Lumpio- @IRCnet etc.
#
# http://lumpio.dy.fi/b/nicofetch.py

import sys
import os, os.path, shutil
import urllib, urllib2, cookielib
import tempfile
import time
import re
import subprocess

from cgi import parse_qs
from urllib import unquote

__all__ = ["error", "VideoInfo", "NicoFetcher"]

debug = False

def js_unescape(s):
    s = unicode(s).replace("\\\"", "\"").replace("\\'", "'")
    s = re.sub(r"\\u([0-9a-fA-F]{4})", lambda mo: unichr(int(mo.group(1), 16)), s)
    s = s.replace("\\\\", "\\");
    return s

def download_file(in_file, out_file, item, progress_listener):
    total_bytes = int(in_file.info().get("Content-Length", 1))
    bytes_read = 0
    
    if progress_listener:
        progress_listener(item, total_bytes, 0, 0)
        
        prev_bytes_read = 0
        bytes_per_second = 0
        interval = 0.5

        start_time = time.clock()
        prev_time = start_time

    while True:
        data = in_file.read(10 * 1024)

        out_file.write(data)
        bytes_read += len(data)

        if len(data) == 0:
            bytes_read = total_bytes

        if progress_listener:
            cur_time = time.time()
            if cur_time >= prev_time + interval or bytes_read == total_bytes:
                if bytes_read == total_bytes:
                    bytes_per_second = int(float(total_bytes) / (cur_time - start_time))
                else:
                    bytes_per_second = int(float(bytes_read - prev_bytes_read) / (cur_time - prev_time))

                progress_listener(item, total_bytes, bytes_read, bytes_per_second)

                prev_bytes_read = bytes_read
                prev_time = cur_time

        if bytes_read == total_bytes:
            break

    out_file.close()
    in_file.close()

class error(Exception):
    pass

class VideoInfo:
    def __init__(self, fetcher):
        self.video_id = None
        self.video_extension = None
        self.thread_id = None
        self.title = None
        self.is_economy = None
        self.watch_url = None
        self.video_url = None
        self.comments_url = None

        self._video_path = None
        self._video_is_temp = False
        self._comments_path = None
        self._comments_is_temp = False
        
        self._fetcher = fetcher
    
    def request_video(self):
        return self._fetcher._request(self.video_url)
    
    def request_comments(self):
        #comments_data = "<thread no_compress=\"0\" user_id=\"0\" when=\"0\" waybackkey=\"0\" res_from=\"-1000\" version=\"20061206\" thread=\"%s\" />" % (self.thread_id)
        comments_data = ("<packet><thread thread=\"{0}\" version=\"20090904\" res_from=\"-1000\" /></packet>").format(self.thread_id)
        return self._fetcher._request(self.comments_url, data=comments_data)
    
    def cleanup(self):
        if self._video_is_temp:
            os.remove(self._video_path)
            self._video_is_temp = False
            self._video_path = None

        if self._comments_is_temp:
            os.remove(self._comments_path)
            self._comments_is_temp = False
            self._comments_path = None

    def _get_path(self, path, default_ext):
        path = os.path.expanduser(path)

        if os.path.exists(path):
            if os.path.isdir(path):
                filename = self.title.replace("/", "").replace("\0", "")
                if self.title != self.video_id:
                    filename += " (" + self.video_id + ")"
                filename += default_ext

                generated_path = os.path.join(path, filename)

                if os.path.exists(generated_path):
                    raise error("File exists: " + generated_path)

                return generated_path
            else:
                raise error("File exists: " + path)
        elif path.endswith("/"):
            raise error("Path ends with / and is not an existent directory")

        return path
    
    def ensure_video_downloaded(self, progress_listener=None):
        if not self._video_path:
            (video_file, video_path) = tempfile.mkstemp(prefix="nicofetch")
            download_file(self.request_video(), os.fdopen(video_file, "w"), "video", progress_listener)
            self._video_path = video_path
            self._video_is_temp = True

    def save_video(self, path, progress_listener=None):
        new_path = self._get_path(path, self.video_extension)
        self.ensure_video_downloaded(progress_listener)

        shutil.move(self._video_path, new_path)
        self._video_path = new_path
        self._video_is_temp = False

    def ensure_comments_downloaded(self, progress_listener=None):
        if not self._comments_path:
            (comments_file, comments_path) = tempfile.mkstemp(prefix="nicofetch")
            download_file(self.request_comments(), os.fdopen(comments_file, "w"), "comments", progress_listener)
            self._comments_path = comments_path
            self._comments_is_temp = True
    
    def save_comments(self, path, progress_listener=None):
        new_path = self._get_path(path, ".xml")
        self.ensure_comments_downloaded()
        
        shutil.move(self._comments_path, new_path)
        self._comments_path = new_path
        self._comments_is_temp = False
    
    def extract_audio(self, path, format=".mp3", progress_listener=None):
        self.ensure_video_downloaded(progress_listener)

        out_path = self._get_path(path, format)

        if self.video_extension == ".flv":
            rv = subprocess.call(["mplayer", "-really-quiet", "-dumpaudio", "-dumpfile", out_path, self._video_path])
        elif self.video_extension == ".swf":
            rv = subprocess.call(["swfextract", "-m", "-o", out_path, self._video_path])
        else:
            rv = subprocess.call(["ffmpeg", "-i", self._video_path, "-vn",
                "-acodec", "copy", out_path])
        
        return rv == 0

class NicoFetcher:
    VIDEO_ID_RE = re.compile(r"(?:/|%2F|^)([a-z]{2}\d+)")
    THUMB_VIDEO_TITLE_RE = re.compile(r"title:\s*'([^']*)'")
    THUMB_KEY_RE = re.compile(r"'thumbPlayKey':\s*'([^']*)'")
    THUMB_MOVIE_TYPE_RE = re.compile(r"movieType:\s*'([^']*)'")
    LOGGED_VIDEO_TITLE_RE = re.compile(r"\(\"wv_title\", \"([^\"]*)\"\)")
    LOGGED_MOVIE_TYPE_RE = re.compile(r"\(\"movie_type\", \"([^\"]*)\"\)")
    #WATCH_VARS_RE = re.compile(r"<embed[^>]+id=\"flvplayer\"[^>]+flashvars=\"([^\"]*)\"", re.I)
    #NOT_LOGGED_IN_RE = re.compile(r"<form[^>]*?id=\"login\"")
    
    def __init__(self):
        self.is_logged_in = False
        self.is_premium = False

        self._cookie_jar = cookielib.LWPCookieJar()
        self._opener = urllib2.build_opener(urllib2.HTTPHandler(debuglevel=debug), urllib2.HTTPCookieProcessor(self._cookie_jar))
    
    def _request_data(self, *args, **kwargs):
        f = self._request(*args, **kwargs)
        data = f.read()
        f.close()
        return data
    
    def _request(self, url, data=None, headers={}):
        if data is not None and not isinstance(data, basestring):
            data = urllib.urlencode(data)

        headers["User-Agent"] = "Mozilla/4.0 (compatible; MSIE 6.0; Windows NT 5.1)"

        req = urllib2.Request(url, data, headers)

        return self._opener.open(req)
    
    def login(self, email, password):
        login_data = self._request("https://secure.nicovideo.jp/secure/login?site=niconico",
            {"mail": email, "password": password})
        
        flag = login_data.info().getheader("x-niconico-authflag")

        self.is_logged_in = (flag in ("1", "3"))
        self.is_premium = (flag == "3")

        return self.is_logged_in

    def _fetch_video_data(self, vid, url, data=None):
        video_data = self._request_data(url, data)

        try:
            getflv_values = parse_qs(video_data)

            vid.thread_id = getflv_values["thread_id"][0]
            vid.video_url = getflv_values["url"][0]
            if vid.video_url[-3:] == "low":
                vid.is_economy = True
            vid.comments_url = getflv_values["ms"][0]
        except:
            raise error("Something went wrong parsing the video_data result")
        
    def _fetch_logged_in(self, vid):
        watch_data = self._request_data("http://www.nicovideo.jp/watch/" + vid.video_id)

        mo = self.LOGGED_MOVIE_TYPE_RE.search(watch_data)
        if mo is None:
            raise error("Error parsing watch result (no movie type)")
        vid.video_extension = "." + mo.group(1).lower()

        mo = self.LOGGED_VIDEO_TITLE_RE.search(watch_data)
        if mo is None:
            vid.title = unicode(video_id)
        else:
            vid.title = unicode(unquote(mo.group(1)), "utf-8")
        
        self._fetch_video_data(vid, "http://flapi.nicovideo.jp/api/getflv", {"v": vid.video_id})
    
    def _fetch_thumb(self, vid):
        thumb_data = self._request_data("http://ext.nicovideo.jp/thumb_watch/" + vid.video_id,
            headers={"Referer": "http://fc2.com/"})

        mo = self.THUMB_KEY_RE.search(thumb_data)
        if mo is None:
            print thumb_data
            raise error("Error parsing thumb_watch result (no key)")
        thumb_key = mo.group(1)

        mo = self.THUMB_MOVIE_TYPE_RE.search(thumb_data)
        if mo is None:
            raise error("Error parsing thumb_watch result (no movie type)")
        vid.video_extension = "." + mo.group(1).lower()

        mo = self.THUMB_VIDEO_TITLE_RE.search(thumb_data)
        if mo is None:
            vid.title = unicode(video_id)
        else:
            vid.title = js_unescape(mo.group(1))
        
        self._fetch_video_data(vid, "http://ext.nicovideo.jp/thumb_watch/" + vid.video_id + "/" + thumb_key)
    
    def fetch(self, video_id):
        vid = VideoInfo(self)

        mo = self.VIDEO_ID_RE.search(video_id)
        if mo is None:
            raise error("No video ID found from video_id")
        
        vid.video_id = mo.group(1)
        vid.watch_url = "http://www.nicovideo.jp/watch/" + vid.video_id

        if self.is_logged_in:
            self._fetch_logged_in(vid)
        else:
            self._fetch_thumb(vid)

        return vid

if __name__ == "__main__":
    import optparse
    import getpass

    quiet = False

    def progress_indicator(item, total_bytes, bytes_read, bytes_per_second):
        bar_length = 40
        bar_fill = int((float(bytes_read) / float(total_bytes)) * float(bar_length))
        # \x1B[2K\x1B[1000D
        print("\r{0}: [{1}] {2}/{3} kB ({4} kB/s)".format(
            item,
            "=" * bar_fill + "-" * (bar_length - bar_fill),
            int(bytes_read / 1024),
            int(total_bytes / 1024),
            int(bytes_per_second / 1024))),
        sys.stdout.flush()

    def cwdify(path):
        return os.getcwd() if path == "-" else path

    def safe_print(msg, error=False):
        try:
            print(msg)
        except:
            print(msg.encode("ascii", "replace"))
    
    class NonWrappingOptionParser(optparse.OptionParser):
        def format_epilog(self, formatter):
            return self.epilog
        def format_description(self, formatter):
            return self.description

    parser = NonWrappingOptionParser(
        description="""nicofetch.py 2.0 - Downloads videos from www.nicovideo.jp

The argument for --video, --audio and --comments can be a file path, or a
directory path in which case a filename is generated from the video title.
Specify '-' to download into the current directory.

If none are specified, the video is downloaded into the current directory.

Logging in is optional, if you do not log in the external player feature is
abused to download a video without an account. For this to work, the
uploader must not have disabled the feature.
""",
        epilog="""
You can store your account information in ~/.nicoacct to avoid having
to enter them every time. Put your e-mail and password in the file on two
separate lines, in that order.

Audio extraction uses the following tools, and they must be in $PATH:
 * .flv   mplayer (must be compiled with FLV support)
 * .mp4   ffmpeg
 * .swf   swfextract (extracts main MP3 stream only, this usually works)
""")
    parser.add_option("-v", "--video",
        help="fetch video into specified path")
    parser.add_option("-c", "--comments",
        help="fetch audio into specified path")
    parser.add_option("-a", "--audio",
        help="fetch comments into specified path")
    parser.add_option("-q", "--quiet", action="store_true",
        help="quiet operation")
    parser.add_option("-e", "--email",
        help="e-mail address for login")
    parser.add_option("-p", "--password",
        help="password for login (specify - to read from stdin)")
    
    (opts, args) = parser.parse_args()

    quiet = opts.quiet

    if not (opts.video or opts.audio or opts.comments):
        opts.video = "-"

    if not args or len(args) != 1:
        parser.print_help()
        safe_print("\nSpecify a single video ID.", True)
        sys.exit(2)

    listener = progress_indicator if not quiet else None

    try:
        fetcher = NicoFetcher()

        email = None
        password = None

        if opts.email and opts.password:
            password = opts.password
            if password == "-":
                password = getpass.getpass("Enter password for " + opts.email + ": ")
        elif opts.email or opts.password:
            safe_print("Specify both email and password to login.", True)
            sys.exit(2)
        else:
            acct_path = os.path.expanduser("~/.nicoacct")
            
            if os.path.exists(acct_path):
                with open(acct_path, "r") as f:
                    data = f.read().splitlines()
                    if len(data) == 2:
                        email = data[0]
                        password = data[1]
                    else:
                        safe_print("Invalid credentials in .nicoacct, ignoring.")
        
        if email and password:
            if not fetcher.login(email, password):
                safe_print("Login error. Check the e-mail and password.", True)
                sys.exit(1)

            user_type = "premium" if fetcher.is_premium else "normal"
            safe_print("Logged in as a {0} user".format(user_type))

        vid = fetcher.fetch(args[0])
        
        if not quiet:
            title = vid.title

            if vid.title != vid.video_id:
                title += " ("  + vid.video_id + ")"

            safe_print(title)

            if vid.is_economy:
                safe_print("Caution: Economy mode in effect. Video quality will be degraded.")

        if opts.video:
            vid.save_video(cwdify(opts.video), progress_listener=listener)
        if opts.comments:
            vid.save_comments(cwdify(opts.comments), progress_listener=listener)
        if opts.audio:
            vid.extract_audio(cwdify(opts.audio), progress_listener=listener)
        
        vid.cleanup()

    except error, e:
        safe_print(u"error: " + unicode(e))
        sys.exit(1)
