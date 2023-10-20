import os
import re
import subprocess
from loguru import logger

r = r"P\d: \[\d+\] \[\d+\] \[\d+m\d+s\]"


def decode(out):
    try:
        # logger.info(out.decode("utf-8"))
        return out.decode("utf-8")
    except UnicodeDecodeError:
        # logger.info(out.decode("gbk"))
        return out.decode("gbk")
    except Exception as e:
        # logger.error(e)
        return e


def bili_download(url, path, args):
    """
    下载b站视频
    :param url: b站视频链接
    :param path: 保存路径
    :param args: bbdown额外参数
        剧集命名 --multi-file-pattern "生活如沸2/生活如沸测试S02E<pageNumberWithZero>"
    :return:
    """
    if not os.path.exists(path):
        os.mkdir(path)
    cmd = (f'bbdown {url} '
           f'-e "hevc,av1,avc" -q "8K 超高清, 1080P 高码率, HDR 真彩, 杜比视界" '
           f'--allow-pcdn --simply-mux --skip-cover {args}')
    p = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=path)
    for i in iter(p.stdout.readline, b""):
        line = decode(i)
        print(line, end='')
    p.wait()
    out, err = p.communicate()
    if p.returncode == 0:
        # res = re.findall(r, out)
        logger.info(f"下载{url}成功，请检查文件夹{path}")
    else:
        err = decode(err)
        logger.error(err)


def cmd_run():
    if not os.path.exists('temp'):
        os.mkdir('temp')
    cmd = ('bbdown https://www.bilibili.com/bangumi/play/ep779129 '
           '-e "hevc,avc" -q "8K 超高清, 1080P 高码率, HDR 真彩, 杜比视界" '
           '--allow-pcdn')
    p = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd='temp')
    out, err = p.communicate()
    if p.returncode == 0:
        out = decode(out)
        res = re.findall(r, out)
        print(res)
    else:
        decode(err)


if __name__ == '__main__':
    cmd_run()
