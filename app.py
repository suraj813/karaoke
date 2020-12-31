from flask import Flask, render_template, make_response, request
from flask_cors import CORS, cross_origin
import requests
import youtube_dl
import time
import sys
from pathlib import Path
import json
import numpy as np
from botocore.exceptions import ClientError
from loguru import logger

app = Flask(__name__)
cors = CORS(app)
model_url = "http://127.0.0.1:8080/predictions/demucs_state/1"
MAX_AUDIO_DURATION = 6500



# get ETA + other things
def get_video_info(url):
    info_dict = youtubedl(url, False)
    response = {
        'url': url,
        'id': info_dict['id'],
        'eta': info_dict['duration'],
        'too_long': int(eta > MAX_AUDIO_DURATION)
    }
    return response


# get the youtube audio in bytes
def youtubedl(url, download=True):
    logger.debug(f"Running youtube-dl for {url}")
    temp = tempfile.TemporaryDirectory()
    ydl_opts = {
        'quiet':True,
        'outtmpl':f'{temp}/%(id)s/original.%(ext)s',
        'format': 'bestaudio/best',
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',}]
        }
    mp3_bytes = None
    with youtube_dl.YoutubeDL(ydl_opts) as ydl:
        info_dict = ydl.extract_info(url, download=download)
        if download:
            out_file = Path(temp) / info_dict['id'] / 'original.mp3'
            info_dict['mp3_bytes'] = open(out_file, 'rb').read()
    return info_dict


# check if torchserve model is up


# get the stems in bytes
def run_inference(mp3_bytes):
    response = requests.post(url=model_url, data=mp3_bytes, headers={'Content-Type': 'audio/mpeg'})
    if response.status_code != 200:
        logger.error(f"HTTP failed with {response.status_code} | {response.text}")
        sys.exit()
    else:
        logger.debug("Inference done! Saving...")
        bytebuf = response.content
        n = len(bytebuf)//4
        stems = [bytebuf[i:i+n] for i in range(0, len(bytebuf), n)]
        source_names = ["drums", "bass", "other", "vocals"]
        stem_bytes = dict(zip(source_names, stems))
        return stem_bytes


class S3Helper:
    def __init__(self, folder):
        self.folder = folder
        self.s3_client = boto3.client('s3')
        self.access_point = S3Helper.get_url() + folder

    # to do - put this in config
    @staticmethod
    def get_url():
        return 'http://' + S3Helper.get_bucket() + '.s3.amazonaws.com/'

    @staticmethod
    def get_bucket():
        return 'jammates-audio'

    # ls grep s3
    def _file_in_cache(self, object_name):
        try:
            r = self.s3_client.head_object(
                Bucket=self.S3_BUCKET,
                Key=object_name)
        except ClientError:
            logger.debug(f"{object_name} doesn't exist in S3 cache")
            return False
        return True

    # upload to s3
    def _upload_file(self, bytes_like, object_name):
        logger.debug(f'Uploading {object_name}')
        try:
            self.s3_client.upload_fileobj(
                io.BytesIO(blob),
                self.S3_BUCKET,
                object_name,
                ExtraArgs={'ACL':'public-read'}})
        return True

    def cache_available():
        return self._file_in_cache(self.folder + '/' + 'vocals.mp3')

    @logger.catch
    def upload_stems(stem_bytes):
        for name, byt in stem_bytes.items():
            obj_name = self.folder + '/' + name + '.mp3'
            self._upload_file(byt, obj_name)
        return True

    # download from s3
    # def _download_file(self, object_name):
    #     x = io.BytesIO()
    #     try:
    #         self.s3_client.download_fileobj(self.S3_BUCKET, object_name, x)
    #         x.seek(0)
    #         blob = x.read()
    #         x.close()
    #     except ClientError as e:
    #         return False
    #     return blob

    # @logger.catch
    # def retrieve_stems():
    #     stem_names = ["drums", "bass", "other", "vocals"]
    #     stems_bytes = {}
    #     for stem in stem_names:
    #         obj_name = self.folder + '/' + stem + '.mp3'
    #         stems_bytes[stem] = self._download_file(obj_name)
    #     return stems_bytes



def validate_url(url):
    info = get_video_info(url)
    if info['too_long']:
        logger.error("URL Validation Failed! Video is too long")
        return False, (413, 'Video too long')
    return True, (200, 'OK')


@app.route("/api/info")
@cross_origin()
def info():
    url = request.args.get('url')
    return get_video_info(url)


@app.route("/api/demux")
@cross_origin()
def main():
    response = {'msg': '', 'status': ''}
    url = request.args.get('url')

    is_valid, status = validate_url(url)
    if not is_valid:
        response['status'], response['msg'] = status
        return response

    info_dict = youtubedl(url)
    s3 = S3Helper(info_dict['id'])
    if not s3.cache_available():
        stem_bytes = run_inference(info_dict['mp3_bytes'])
        upload_stems(stem_bytes)
    response['status'] = 200
    response['msg'] = s3.access_point

    return response


if __name__ == "__main__":
    app.run(debug=True)