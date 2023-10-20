import re
import os
import cn2an
import requests
import argparse
import tempfile
from PIL import Image
from pathlib import Path
from xpinyin import Pinyin
from typing import Optional
from loguru import logger
from pymediainfo import MediaInfo
from differential.utils.binary import execute
from differential.plugins.nexusphp import NexusPHP
from differential.plugins.bbdown import bili_download
from differential.utils.torrent import make_torrent
from differential.utils.mediainfo import (
    get_resolution,
    get_duration,
)

cleaned_re = r'\s+'
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
        self.bilibili_url = bilibili_url
        self.bilibili_save_path = bilibili_save_path
        self.custom_screenshot_path = custom_screenshot_path

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
        temp_name = re.sub(r'[\u4e00-\u9fa5]', '', temp_name).strip()
        temp_name = re.sub(chinese_mark_re, '', temp_name).strip()
        return temp_name

    @property
    def subtitle(self):
        if not self._ptgen.get("site") == "douban":
            return ""
        if "chinese_title" in self._ptgen:
            subtitle = f"{'/'.join([self._ptgen.get('chinese_title')] + self._ptgen.get('aka', []))}"
        else:
            subtitle = f"{'/'.join(self._ptgen.get('aka', []))}"
        if self.custom_episode:
            subtitle += f" 第{self.custom_episode}集"
        if self._ptgen.get("director"):
            subtitle += (
                f" | 导演：{'/'.join([d.get('name') for d in self._ptgen.get('director')])}"
            )
        if self._ptgen.get("writer"):
            subtitle += (
                f" | 编剧：{'/'.join([w.get('name') for w in self._ptgen.get('writer')])}"
            )
        if self._ptgen.get("cast"):
            subtitle += (
                f" | 主演：{'/'.join([c.get('name') for c in self._ptgen.get('cast')[:3]])}"
            )
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
                        image.save(f"{screenshot_path}", format="PNG", optimized=True)
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
                        image.save(f"{screenshot_path}", format="PNG", optimized=True)
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
        elif self._ptgen.get("current_season"):
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
        filename += "WEB-DL."
        if self.audio_codec:
            filename += f"{self.audio_codec.upper()}."
        if self.quality:
            filename += f"{self.quality}."
        if self.video_codec:
            filename += f"{self.video_codec.upper()}"
        filename += "-HDSWEB"

        # filename = re.sub(chinese_mark_re, '', filename)
        filename = re.sub(windows_special_char, '', filename)
        if self.generate_name:
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
            "Dolby Digital Plus": "ddp",
            "Dolby Digital": "dd",
            "DTS-HD Master Audio": "dtshdma",
            "Dolby Digital Plus with Dolby Atmos": "atmos",
            "Dolby TrueHD": "truehd",
            "Dolby TrueHD with Dolby Atmos": "truehd",
            "AAC": "aac",
            "HE-AAC": "aac",
            "Audio Coding 3": "ac3",
            "Free Lossless Audio Codec": "flac",
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
                return "h264"
            if track.commercial_name == "HEVC":
                return "h265"
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

    def auto_feed_info(self):
        info = {
            'name': 'Ancient Powers S01 2023 1080p WEB-DL AAC H265-HDSWEB',
            'small_descr': '亘古文明/Ancients | 主演：萨利玛·伊克 拉姆 Salima Ikram',
            'url': 'https://www.imdb.com/title/tt0133093/',
            'dburl': 'https://movie.douban.com/subject/36406123',
            'descr': '[img]https://img1.doubanio.com/view/photo/l_ratio_poster/public/p2892043049.jpg[/img]\n\n◎译\u3000\u3000名\u3000亘古文明 / Ancients\n◎片\u3000\u3000名\u3000Ancient Powers\n◎年\u3000\u3000代\u30002023\n◎产\u3000\u3000地\u3000英国 / 中国大陆\n◎类\u3000\u3000别\u3000纪录片 / 历史\n◎语\u3000\u3000言\u3000英语\n◎上映日期\u30002023-07-07(中国大 陆)\n◎IMDb链接\u3000https://www.imdb.com/title//\n◎豆瓣评分\u30008.0/10 (356人评价)\n◎豆瓣链接\u3000https://movie.douban.com/subject/36406123/\n◎集\u3000\u3000数\u30006\n◎片\u3000\u3000长\u300050分钟\n◎演\u3000\u3000员\u3000萨利玛·伊克拉姆 Salima Ikram (id:1495243)\n\n◎简\u3000\u3000介\n\u3000\u3000纪录片展现出五大古老文明走过的非凡历程，以全新的角度回溯久远历史，融汇东西方世界的动人故事。这些悠远而强大的力量，在历史长河中不断应对社会、技术和现实挑战——贸易、战争和奇思妙想，曾将世界紧密相连，而当面对同一难题时，各地区却采取了截然不同的对策。如今，逝去的世界得益于先进科技、考古学发现和精美CGI的加持，得以重现于世——这是一部关于策略和运气的史诗。\n\n[img]https://m.hdsky.me/adv/hdsweb_logo.png[/img]\n[color=Blue][b]【影片参数】[/b][/color]\n[quote][b][size=3][color=Blue]亘古文明.Ancient.Powers.S01E01.2023.1080p.WEB-DL.AAC.H265-HDSWEB[/color][/size][/b]\n\n[b]General Information: [/b][font=monospace]\n\nRELEASE.NAME........: 亘古文明.Ancient.Powers.S01E01.2023.1080p.WEB-DL.AAC.H265-HDSWEB\r\nRELEASE.DATE........: UTC 2023-10-20 14:36:31.647\r\nDURATION............: 00:47:59.083 (HH:MM:SS.MMM)\r\nRELEASE.SIZE........: 666 MiB\r\nRELEASE.FORMAT......: MPEG-4\r\nOVERALL.BITRATE.....: 1 941 kb/s\r\nRESOLUTION..........: 1920 x 1080 (16:9)\r\nVIDEO.CODEC.........: HEVC Main@L5@Main @ 1 730 kb/s\r\nBIT.DEPTH...........: 8 bits\r\nFRAME.RATE..........: 25.000 FPS\r\nAspect.Ratio........: 1.778\r\nAudio #0............: CBR AAC LC 2 channels @ 204 kb/s \r\nSOURCE..............: WEB-DL\r\nUPLOADER............: Anonymous@HDSWEB\n[/font][/quote]\n[b][color=Blue]【截图赏析】[/color][/b]\n[img]https://img.tucang.cc/api/image/show/5e524afcb9382e5db788995a86b1df47[/img]\n[img]https://img.tucang.cc/api/image/show/5b4bf7b7f26f30eb296c3c2085eb3482[/img]\n[img]https://img.tucang.cc/api/image/show/95c532b25d69513b965c3e3051c1e0da[/img]\n[img]https://img.tucang.cc/api/image/show/38d7f097588bcaa243925a0d9106990c[/img]\n[img]https://img.tucang.cc/api/image/show/11d52f1a0f0d26d21c59da4660ef9ee6[/img]',
            'log_info': '',
            'tracklist': '',
            'music_type': '',
            'music_media': '',
            'animate_info': '',
            'anidb': '',
            'torrent_name': '',
            'images': ['https://img.tucang.cc/api/image/show/5e524afcb9382e5db788995a86b1df47',
                       'https://img.tucang.cc/api/image/show/5b4bf7b7f26f30eb296c3c2085eb3482',
                       'https://img.tucang.cc/api/image/show/95c532b25d69513b965c3e3051c1e0da',
                       'https://img.tucang.cc/api/image/show/38d7f097588bcaa243925a0d9106990c',
                       'https://img.tucang.cc/api/image/show/11d52f1a0f0d26d21c59da4660ef9ee6'],
            'type': '电影',
            'source_sel': '大陆',
            'standard_sel': '1080p',
            'audiocodec_sel': 'AAC',
            'codec_sel': 'H265',
            'medium_sel': 'web-dl',
            'origin_site': '',
            'origin_url': '',
            'golden_torrent': False,
            'mediainfo_cmct': '',
            'imgs_cmct': '',
            'full_mediainfo': 'General\nComplete name: I:\\HDSky\\电视剧发种\\亘古文明.Ancient.Powers.S01.2023.1080p.WEB-DL.AAC.H265-HDSWEB\\亘古文明.Ancient.Powers.S01E01.2023.1080p.WEB-DL.AAC.H265-HDSWEB.mp4\nFormat: MPEG-4\nFile Size: 666 MiB\nDuration: 47 min 59 s\nOverall bit rate: 1 941 kb/s\nWriting application: Lavf60.15.100\n\nVideo\nID: 1\nFormat: HEVC\nFormat/Info: High Efficiency Video Coding\nFormat profile: Main@L5@Main\nCodec ID: hev1\nDuration: 47 min 59 s\nBit rate: 1 730 kb/s\nWidth: 1 920 pixels\nHeight: 1 080 pixels\nDisplay aspect ratio: 16:9\nFrame rate mode: Constant\nFrame rate: 25.000 FPS\nColor space: YUV\nChroma subsampling: 4:2:0\nBit depth: 8 bits\nBits/(Pixel*Frame): 0.033\nStream size: 594 MiB (89%)\nColor range: Limited\nColor primaries: BT.709\nTransfer characteristics: BT.709\nMatrix coefficients: BT.709\n\nAudio\nID: 2\nFormat: AAC LC\nFormat/Info: Advanced Audio Codec Low Complexity\nCommercial name: AAC\nCodec ID: mp4a-40-2\nDuration: 47 min 59 s\nBit rate mode: Constant\nBit rate: 204 kb/s\nChannel(s): 2\nChannel layout: L R\nSampling rate: 48.0 kHz\nFrame rate: 46.875 FPS (1024 SPF)\nCompression mode: Lossy\nStream size: 70.1 MiB (11%)\nDefault: Yes\n\n',
            'subtitles': [],
            'youtube_url': '',
            'ptp_poster': '',
            'comparisons': ''
        }
        pass

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
