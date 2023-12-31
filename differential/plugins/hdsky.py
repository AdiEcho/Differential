import re
import os
import cn2an
import requests
import argparse
import tempfile
from PIL import Image
from io import BytesIO
from pathlib import Path
from xpinyin import Pinyin
from typing import Optional
from loguru import logger
from pymediainfo import MediaInfo
import mozjpeg_lossless_optimization
from configparser import ConfigParser
from differential.utils.binary import execute
from differential.plugins.nexusphp import NexusPHP
from differential.plugins.bbdown import bili_download
from differential.utils.torrent import make_torrent
from differential.utils.mediainfo import (
    get_resolution,
    get_duration,
)

cleaned_re = r'\s+'
chinese_re = r'[\u4e00-\u9fa5]'
chinese_mark_re = r"[\u3000-\u303f\uFF00-\uFFEF]"
chinese_season_re = r"第\s*([一二三四五六七八九十百\d]+)\s*季"
english_season_re = r"[Ss]eason[\s+\.]([0-9+])"
windows_special_char = r"[\/\\\:\*\?\"\<\>\|]"
p = Pinyin()


def is_all_english(strs):
    import string
    for i in strs:
        if i not in string.printable:
            return False
    return True


def extract_chinese_name(full_name):
    match = re.search(f"{chinese_re}+", full_name)
    if match:
        return match.group()
    else:
        return ""


def process_aka_name(aka_name):
    aka_name = aka_name.strip().replace(',', ' ').replace('-', ' ')
    aka_name = re.sub(cleaned_re, ' ', aka_name)
    aka_name = aka_name.replace(' ', '.')
    return aka_name


class HDSky(NexusPHP):

    @classmethod
    def get_aliases(cls):
        return 'hdsky',

    @classmethod
    def get_help(cls):
        return 'HDSky插件，适用于HDSky'

    @classmethod
    def add_parser(cls, parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
        parser = super().add_parser(parser)
        parser.add_argument('--custom_format_path', type=str, help="自定义MediaInfo输出格式路径",
                            default=argparse.SUPPRESS)
        parser.add_argument('--generate_name', type=str, help="是否根据pt_gen生成文件夹名字",
                            default=argparse.SUPPRESS)
        parser.add_argument('--custom_aka_name', type=str, help="自定义外文名", default=argparse.SUPPRESS)
        parser.add_argument('--custom_season', type=str, help="自定义季数", default=argparse.SUPPRESS)
        parser.add_argument('--custom_episode', type=str, help="自定义集数", default=argparse.SUPPRESS)
        parser.add_argument('--bilibili_url', type=str, help="bilibili视频链接", default=argparse.SUPPRESS)
        parser.add_argument('--bilibili_save_path', type=str, help="bilibili视频保存路径",
                            default=argparse.SUPPRESS)
        parser.add_argument("--custom_screenshot_path", type=str, help="截图保存路径", default=argparse.SUPPRESS)
        parser.add_argument("--tv_unfinished", type=str, help="是否连载", default=argparse.SUPPRESS)
        parser.add_argument("--use_folder_episode", type=str, help="是否基于文件夹内的集数生成副标题",
                            default=argparse.SUPPRESS)
        parser.add_argument("--platform", type=str, help="视频源平台，存在时才添加", default=argparse.SUPPRESS)
        return parser

    def __init__(
            self,
            custom_format_path: str = r" HDSWEB.csv",
            generate_name: str = "false",
            custom_aka_name: str = "",
            custom_season: str = "",
            custom_episode: str = "",
            bilibili_url: str = "",
            bilibili_save_path: str = "",
            custom_screenshot_path: str = "",
            tv_unfinished: str = "",
            use_folder_episode: str = "",
            platform: str = "",
            **kwargs,
    ):
        super().__init__(upload_url="https://hdsky.me/upload.php", **kwargs)
        self.generate_name: bool = False
        self.custom_format_path = f"file://{custom_format_path}"
        if generate_name == "true" or generate_name == "True" or generate_name == "yes" or generate_name == "Yes":
            self.generate_name: bool = True
            self.screenshot_count = 0
        self.custom_aka_name = custom_aka_name
        self.custom_season = str(custom_season)
        self.custom_episode = custom_episode
        self.use_folder_episode = use_folder_episode
        self.bilibili_url = bilibili_url
        self.bilibili_save_path = bilibili_save_path
        self.custom_screenshot_path = custom_screenshot_path
        self.tv_unfinished = tv_unfinished
        self.platform = platform
        self.config_path = "\\".join(kwargs["config"].split("\\")[:-1])

    def _prepare(self):
        ptgen_retry = 2 * self.ptgen_retry
        self._ptgen = self._get_ptgen()
        while self._ptgen.get("failed") and ptgen_retry > 0:
            self._ptgen = self._get_ptgen(ptgen_retry <= self.ptgen_retry)
            ptgen_retry -= 1
        if self.bilibili_url and self.bilibili_save_path:
            self.bili_temp_download()
            self._mediainfo = self._find_mediainfo()
            self.bili_auto_download()
        self._mediainfo = self._find_mediainfo()
        if self.generate_nfo:
            self._generate_nfo()
        self._screenshots = self._get_screenshots()
        if self.make_torrent:
            make_torrent(
                self.folder,
                self.announce_url,
                self.__class__.__name__,
                self.reuse_torrent,
                self.from_torrent,
            )
        if self.bilibili_url and self.bilibili_save_path:
            os.remove(os.path.join(self.bilibili_save_path, "temp", f"{self.douban_id}.mp4"))

    @property
    def category(self):
        if self.tv_unfinished == "true" or self.tv_unfinished == "True" or self.tv_unfinished == "Yes":
            return "tvUnfinished"
        if "演唱会" in self._ptgen.get("tags", []) and "音乐" in self._ptgen.get(
            "genre", []
        ):
            return "concert"
        imdb_genre = self._imdb.get("genre", [])
        if "Documentary" in imdb_genre:
            return "documentary"
        imdb_type = self._imdb.get("@type", "")
        if imdb_type == "Movie":
            return "movie"
        if imdb_type == "TVSeries":
            return "tvPack"
        return imdb_type

    @property
    def team(self):
        return "HDSWEB/网络视频小组"

    @property
    def mediainfo(self):
        return self._mediainfo

    @property
    def title(self):
        # TODO: Either use file name or generate from mediainfo and ptgen
        temp_name = (
            self.folder.name if self.folder.is_dir() else self.folder.stem
        ).replace(".", " ")
        temp_name = re.sub(r"(?<=5|7)( )1(?=.*$)", ".1", temp_name)
        temp_name = re.sub(chinese_re, '', temp_name).strip()
        temp_name = re.sub(chinese_mark_re, '', temp_name).strip()
        temp_name = re.sub(cleaned_re, ' ', temp_name).strip()
        return temp_name

    @property
    def subtitle(self):
        if not self._ptgen.get("site") == "douban":
            return ""

        if (self.custom_episode or self.custom_season or self.use_folder_episode or
                len(re.findall(self._ptgen.get("chinese_title").strip(), chinese_season_re)) > 0):
            if "chinese_title" in self._ptgen:
                subtitle = f"{self._ptgen.get('chinese_title')}"
            else:
                subtitle = f"{'/'.join(self._ptgen.get('aka', []))}"

            if self.custom_episode:
                subtitle += f" 第{'-'.join(self.custom_episode.split(','))}集"
            elif self.use_folder_episode == "True" or self.use_folder_episode == "true":
                seasons = []
                episodes = []
                for _, _, filenames in os.walk(self.folder):
                    for filename in filenames:
                        res = re.findall(r"\.S(\d+)E(\d+)\.", filename)
                        if not res:
                            continue
                        if res[0][0] not in seasons:
                            seasons.append(int(res[0][0]))
                        if res[0][1] not in episodes:
                            episodes.append(int(res[0][1]))
                episodes = sorted(episodes)
                if seasons[0] != 1:
                    subtitle += f" 第 {str(seasons[0])} 季"
                if len(episodes) > 1:
                    subtitle += f" 第 {episodes[0]}-{episodes[-1]} 集"
        else:
            if "chinese_title" in self._ptgen:
                subtitle = f"{'/'.join([self._ptgen.get('chinese_title')] + self._ptgen.get('aka', []))}"
            else:
                subtitle = f"{'/'.join(self._ptgen.get('aka', []))}"
            if self._ptgen.get("director"):
                subtitle += (
                    f" | 导演：{'/'.join([d.get('name') for d in self._ptgen.get('director')])}"
                )
            if self._ptgen.get("writer"):
                subtitle += (
                    f" | 编剧：{'/'.join([w.get('name') for w in self._ptgen.get('writer')])}"
                )
        if self._ptgen.get("cast"):
            chinese_cast = [extract_chinese_name(c.get('name')) for c in self._ptgen.get('cast')[:3] if extract_chinese_name(c.get('name'))]
            if "" in chinese_cast:
                chinese_cast.remove("")
            subtitle += f" | 主演：{'  /  '.join(chinese_cast)}"
        if self.platform.lower() == "nf":
            subtitle += f" [内封简繁英等多国字幕]"
        return subtitle

    @property
    def iyuu_ptgen(self):
        url = "https://api.iyuu.cn/App.Movie.Ptgen"
        params = {
            "url": self.douban_url
        }
        response = requests.get(url, params=params)
        if response.ok:
            return response.json().get("data", {}).get("format", "")
        else:
            return ""

    def _make_screenshots(self) -> Optional[str]:
        # TODO https://nicelee.top/blog/2021/01/06/python-opencv-video-frame/
        resolution = get_resolution(self._main_file, self._mediainfo)
        duration = get_duration(self._mediainfo)
        if resolution is None or duration is None:
            return None

        temp_dir = None
        # 查找已有的截图
        if self.screenshot_path:
            for f in Path(self.screenshot_path).glob(
                    self.image_hosting.value
            ):
                if f.is_dir() and self.folder.name in f.name:
                    if 0 < self.screenshot_count == len(list(f.glob("*.png"))):
                        temp_dir = f.absolute()
                        logger.info("发现已生成的{}张截图，跳过截图...".format(self.screenshot_count))
                        break
            else:
                os.mkdir(f"{self.screenshot_path}/{self.folder.name}")
                # 生成截图
                for i in range(1, self.screenshot_count + 1):
                    logger.info(f"正在生成第{i}张截图...")
                    t = int(i * duration / (self.screenshot_count + 1))
                    screenshot_path = (
                        f"{temp_dir}/{self._main_file.stem}.thumb_{str(i).zfill(2)}.png"
                    )
                    execute(
                        "ffmpeg",
                        (
                            f'-y -ss {t}ms -skip_frame nokey -i "{self._main_file.absolute()}" '
                            f'-s {resolution} -vsync 0 -vframes 1 -c:v png "{screenshot_path}"'
                        ),
                    )
                    if self.optimize_screenshot:
                        image = Image.open(screenshot_path)
                        jpeg_io = BytesIO()
                        image.convert("RGB").save(jpeg_io, format="JPEG")
                        jpeg_io.seek(0)
                        jpeg_bytes = jpeg_io.read()
                        optimized_jpeg_bytes = mozjpeg_lossless_optimization.optimize(jpeg_bytes)
                        # w, h = image.size
                        # new_width = 1920
                        # new_height = int(1920 * h / w)
                        # resize_img = image.resize((new_width, new_height))
                        # image.save(screenshot_path, format="PNG", optimized=True)
                        with open(screenshot_path, "wb") as f:
                            f.write(optimized_jpeg_bytes)
        else:
            for f in Path(tempfile.gettempdir()).glob(
                    "Differential.screenshots.{}.*".format(self.image_hosting.value)
            ):
                if f.is_dir() and self.folder.name in f.name:
                    if 0 < self.screenshot_count == len(list(f.glob("*.png"))):
                        temp_dir = f.absolute()
                        logger.info("发现已生成的{}张截图，跳过截图...".format(self.screenshot_count))
                        break
            else:
                temp_dir = tempfile.mkdtemp(
                    prefix="Differential.screenshots.{}.".format(
                        self.image_hosting.value
                    ),
                    suffix=self.folder.name,
                )
                # 生成截图
                for i in range(1, self.screenshot_count + 1):
                    logger.info(f"正在生成第{i}张截图...")
                    t = int(i * duration / (self.screenshot_count + 1))
                    screenshot_path = (
                        f"{temp_dir}/{self._main_file.stem}.thumb_{str(i).zfill(2)}.png"
                    )
                    execute(
                        "ffmpeg",
                        (
                            f'-y -ss {t}ms -skip_frame nokey -i "{self._main_file.absolute()}" '
                            f'-s {resolution} -vsync 0 -vframes 1 -c:v png "{screenshot_path}"'
                        ),
                    )
                    if self.optimize_screenshot:
                        image = Image.open(screenshot_path)
                        jpeg_io = BytesIO()
                        image.convert("RGB").save(jpeg_io, format="JPEG")
                        jpeg_io.seek(0)
                        jpeg_bytes = jpeg_io.read()
                        optimized_jpeg_bytes = mozjpeg_lossless_optimization.optimize(jpeg_bytes)
                        # w, h = image.size
                        # new_width = 1920
                        # new_height = int(1920 * h / w)
                        # resize_img = image.resize((new_width, new_height))
                        # image.save(screenshot_path, format="PNG", optimized=True)
                        with open(screenshot_path, "wb") as f:
                            f.write(optimized_jpeg_bytes)
        return temp_dir

    @property
    def description(self):
        if self.generate_name:
            self.generate_filename()

        before_media_info = """[img]https://m.hdsky.me/adv/hdsweb_logo.png[/img]
[color=Blue][b]【影片参数】[/b][/color]"""
        before_screen_shot = """[b][color=Blue]【截图赏析】[/color][/b]"""
        enter = "\n"
        ptgen_info = self.iyuu_ptgen
        if not ptgen_info:
            ptgen_info = self._ptgen.get("format").replace("img1.doubanio.com", "img9.doubanio.com")
        return (f'{ptgen_info}\n\n'
                f'{before_media_info}\n'
                f'[quote][b][size=3][color=Blue]{self.release_name}[/color][/size][/b]\n\n'
                f'[b]General Information: [/b][font=monospace]\n\n'
                f'{self.media_info}'
                f'{f"{enter}{enter}" + self.parsed_encoder_log if self.parsed_encoder_log else ""}\n'
                f'[/font][/quote]\n'
                f'{before_screen_shot}\n'
                f'{f"{enter}".join([f"{uploaded}" for uploaded in self._screenshots])}')

    def generate_filename(self, gen_bbdown=False):
        filename = ""
        season = ""
        if self._ptgen.get("chinese_title"):
            chinese_title = self._ptgen.get("chinese_title").strip()
            seasons = re.findall(chinese_season_re, chinese_title)
            if seasons:
                if seasons[0].isdigit():
                    season = seasons[0]
                else:
                    season = str(cn2an.cn2an(seasons[0]))
            chinese_title = re.sub(chinese_season_re, "", chinese_title)
            chinese_title = re.sub(cleaned_re, " ", chinese_title).strip()
            chinese_title = chinese_title.replace(" ", "")
            filename += f"{chinese_title}."

        aka_name = None
        if self.custom_aka_name:
            aka_name = process_aka_name(self.custom_aka_name)
        elif self._ptgen.get('foreign_title') and is_all_english(self._ptgen.get('foreign_title')):
            aka_name = process_aka_name(self._ptgen.get('foreign_title'))
        elif self._ptgen.get("aka"):
            for aka in self._ptgen.get("aka"):
                if is_all_english(aka):
                    aka_name = process_aka_name(aka)
                    break

        if aka_name:
            _seasons = re.findall(english_season_re, aka_name)
            if _seasons and _seasons[0] == season:
                aka_name = re.sub(english_season_re, "", aka_name).strip('.').strip()
            filename += f"{aka_name}."
        else:
            chinese_title = p.get_pinyin(self._ptgen.get('chinese_title').strip()).replace('-', '.').title()
            filename += f"{chinese_title}."

        if self.custom_season:
            season = f"S0{self.custom_season.strip()}" \
                if len(self.custom_season.strip()) == 1 \
                else f"S{self.custom_season.strip()}"
        elif season:
            season = f"S0{season}" \
                if len(season) == 1 \
                else f"S{season}"
        elif self._ptgen.get("current_season") and self._imdb.get("@type", "") != "Movie":
            season = f"S0{self._ptgen.get('current_season').strip()}" \
                if len(self._ptgen.get('current_season').strip()) == 1 \
                else f"S{self._ptgen.get('current_season').strip()}"

        episode = ""
        if not gen_bbdown:
            if self.custom_episode:
                episodes = self.custom_episode.split(",")
                if len(episodes) == 1:
                    episode = f"E0{self.custom_episode.strip()}" \
                        if len(self.custom_episode.strip()) == 1 \
                        else f"E{self.custom_episode.strip()}"
                else:
                    first = f"E0{episodes[0].strip()}" \
                        if len(episodes[0].strip()) == 1 \
                        else f"E{episodes[0].strip()}"
                    last = f"E0{episodes[-1].strip()}" \
                        if len(episodes[-1].strip()) == 1 \
                        else f"E{episodes[-1].strip()}"
                    episode = f"{first}-{last}"
        else:
            episode = "#replace_episode#"

        filename += f"{season}{episode}." if season and episode else (f"{season}." if season else "")

        if self._ptgen.get("year"):
            filename += f"{self._ptgen.get('year').strip()}."
        if self.resolution:
            filename += f"{self.resolution}."
        if self.platform:
            filename += f"{self.platform.upper()}."
        filename += "WEB-DL."
        if self.audio_codec:
            filename += f"{self.audio_codec}."
        if self.video_codec:
            filename += f"{self.video_codec.replace('x', 'H') if self.platform != '' else self.video_codec}."
        if self.quality:
            filename += f"{self.quality}"
        filename += "-HDSWEB"

        # filename = re.sub(chinese_mark_re, '', filename)
        filename = re.sub(windows_special_char, '', filename)
        filename = filename.replace(".-", "-")
        if self.generate_name:
            # TODO 将文件夹内的文件名按照filename格式化
            if self._imdb.get("@type", "") == "Movie":
                for _, _, filenames in os.walk(self._main_file.parent):
                    _filename = filenames[0]
                    origin_file_extension = _filename.split(".")[-1]
                    os.rename(os.path.join(self._main_file.parent, _filename), os.path.join(self._main_file.parent, f"{filename}.{origin_file_extension}"))
                os.rename(self._main_file.parent, os.path.join(self._main_file.parent.parent, filename))
                config_path = f"{self.config_path}\\hdsky_netflix.ini"
                config = ConfigParser()
                config.read_file(open(config_path, "r", encoding="utf-8"))
                config.set("HDSky", "url", self.url)
                config.set("HDSky", "folder", os.path.join(self._main_file.parent.parent, filename))
                config.write(open(config_path, "w", encoding="utf-8"))
            elif self._imdb.get("@type", "") == "TVSeries":
                for _, _, filenames in os.walk(self._main_file.parent):
                    for _filename in filenames:
                        origin_file_extension = _filename.split(".")[-1]
                        result = re.findall(r"\.S(\d+)E(\d+)\.", _filename)
                        _season = result[0][0]
                        _episodes = result[0][1]
                        result_split = filename.split(f".S{_season}.")
                        new_filename = f".S{_season}E{_episodes}.".join(result_split)
                        new_filename += f".{origin_file_extension}"
                        os.rename(os.path.join(self._main_file.parent, _filename), os.path.join(self._main_file.parent, new_filename))
                os.rename(self._main_file.parent, os.path.join(self._main_file.parent.parent, filename))
                config_path = f"{self.config_path}\\hdsky_netflix.ini"
                config = ConfigParser()
                config.read_file(open(config_path, "r", encoding="utf-8"))
                config.set("HDSky", "url", self.url)
                config.set("HDSky", "folder", os.path.join(self._main_file.parent.parent, filename))
                config.write(open(config_path, "w", encoding="utf-8"))
            else:
                with open(f"{self._main_file.parent}/filename.txt", "w", encoding="utf-8") as f:
                    f.write(filename)
            exit(0)
        return filename

    @property
    def video_type(self):
        if "webdl" in self.folder.name.lower() or "web-dl" in self.folder.name.lower():
            return "WEB-DL"
        elif "remux" in self.folder.name.lower():
            return "Remux"
        elif "hdtv" in self.folder.name.lower():
            return "HDTV"
        elif any(e in self.folder.name.lower() for e in ("x264", "x265")):
            return "Encode"
        elif "bluray" in self.folder.name.lower() and not any(
                e in self.folder.name.lower() for e in ("x264", "x265")
        ):
            return "Blu-ray"
        elif "uhd" in self.folder.name.lower():
            return "UHD Blu-ray"
        for track in self._mediainfo.tracks:
            if track.track_type == "Video":
                if track.encoding_settings:
                    return "Encode"
        return ""

    @property
    def media_info(self):
        return MediaInfo.parse(self._main_file, output=self.custom_format_path)

    @property
    def audio_codec(self):
        codec_map = {
            "Dolby Digital Plus": "DDP",
            "Dolby Digital": "DD",
            "DTS-HD Master Audio": "DTSHDMA",
            "Dolby Digital Plus with Dolby Atmos": "Atmos",
            "Dolby TrueHD": "TrueHD",
            "Dolby TrueHD with Dolby Atmos": "TrueHD",
            "AAC": "AAC",
            "HE-AAC": "AAC",
            "Audio Coding 3": "AC3",
            "Free Lossless Audio Codec": "FLAC",
        }
        normal_codec_list = ["Audio Coding 3", "Free Lossless Audio Codec", "AAC", "HE-AAC"]
        dolby_codec = ""
        normal_codec = ""
        for track in self._mediainfo.audio_tracks:
            commercial_name = track.commercial_name
            format_info = track.format_info

            if commercial_name in codec_map:
                if format_info in normal_codec_list:
                    normal_codec = codec_map[commercial_name]
                else:
                    dolby_codec = codec_map[commercial_name]
            if track.channel_s == 6:
                dolby_codec = "DDP5.1." + dolby_codec if dolby_codec and dolby_codec != "DDP" else "DDP5.1"
            elif track.channel_s == 2:
                dolby_codec = "DDP2.0." + dolby_codec if dolby_codec and dolby_codec != "DDP" else "DDP2.0"
            # TODO: other formats
            # dts: "3",
            # lpcm: "21",
            # dtsx: "3",
            # ape: "2",
            # wav: "22",
            # mp3: "4",
            # m4a: "5",
            # other: "7"
        return f"{dolby_codec if dolby_codec else ''}{'.' if dolby_codec and normal_codec else ''}{normal_codec}"

    @property
    def video_codec(self):
        for track in self._mediainfo.video_tracks:
            if track.encoded_library_name:
                return track.encoded_library_name
            if track.commercial_name == "AVC":
                return "H264"
            if track.commercial_name == "HEVC":
                return "H265"
        #  h264: "AVC/H.264",
        #  hevc: "HEVC",
        #  x264: "x264",
        #  x265: "x265",
        #  h265: "HEVC",
        #  mpeg2: "MPEG-2",
        #  mpeg4: "AVC/H.264",
        #  vc1: "VC-1",
        #  dvd: "MPEG"
        return ""

    @property
    def quality(self):
        for track in self._mediainfo.video_tracks:
            if track.hdr_format:
                if "Dolby Vision" in track.hdr_format:
                    return "DV"
                if "HDR10" in track.hdr_format:
                    return "HDR10"
            else:
                return ""
        return ""

    @property
    def release_name(self):
        return self._main_file.stem

    @property
    def douban_id(self):
        return self._ptgen.get("douban_id")

    def bili_temp_download(self):
        bili_download(self.bilibili_url, self.bilibili_save_path, f"--multi-file-pattern temp/{self.douban_id}.mp4")
        self.folder = Path(self.bilibili_save_path).joinpath("temp")

    def bili_auto_download(self):
        name = self.generate_filename(gen_bbdown=True)
        pathname = name.replace("#replace_episode#", "")
        filename = name.replace("#replace_episode#", "E<pageNumberWithZero>")
        logger.info(f"bilibili视频 文件夹名：{pathname}")
        logger.info(f"bilibili视频 文件名：{filename}")
        if input("请确认文件名是否正确，按回车继续，输入内容退出") != "":
            exit(0)
        args = f'-p ALL --multi-file-pattern "{pathname}/{filename}"'
        bili_download(self.bilibili_url, self.bilibili_save_path, args)
        self.folder = Path(self.bilibili_save_path).joinpath(pathname)
