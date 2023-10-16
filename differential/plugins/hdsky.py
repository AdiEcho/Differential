import re
import requests
import argparse
from xpinyin import Pinyin
from pymediainfo import MediaInfo
from differential.plugins.nexusphp import NexusPHP

cleaned_re = r'\s+'
chinese_mark_re = r"[\u3000-\u303f\uFF00-\uFFEF]"
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
        parser.add_argument('--custom_aka_name', type=str, help="自定义外文名",
                            default=argparse.SUPPRESS)
        parser.add_argument('--custom_season', type=str, help="自定义季数",
                            default=argparse.SUPPRESS)
        parser.add_argument('--custom_episode', type=str, help="自定义集数",
                            default=argparse.SUPPRESS)
        return parser

    def __init__(
            self,
            custom_format_path: str = r"E:\HDSky\HDSWEB软件\MediaInfo_GUI_23.09\Plugin\Custom\HDSWEB.csv",
            generate_name: str = "false",
            custom_aka_name: str = "",
            custom_season: str = "",
            custom_episode: str = "",
            **kwargs,
    ):
        super().__init__(upload_url="https://hdsky.me/upload.php", **kwargs)
        self.generate_name: bool = False
        self.custom_format_path = f"file://{custom_format_path}"
        if generate_name == "true" or generate_name == "True" or generate_name == "yes" or generate_name == "Yes":
            self.generate_name: bool = True
            self.screenshot_count = 0
        self.custom_aka_name = custom_aka_name
        self.custom_season = custom_season
        self.custom_episode = custom_episode

    @property
    def title(self):
        # TODO: Either use file name or generate from mediainfo and ptgen
        temp_name = (
            self.folder.name if self.folder.is_dir() else self.folder.stem
        ).replace(".", " ")
        temp_name = re.sub(r"(?<=5|7)( )1(?=.*$)", ".1", temp_name)
        temp_name = re.sub(r'[\u4e00-\u9fa5]', '', temp_name).strip()
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
                f'[quote][b][size=3][color=Blue]{self.release_name.replace(".", " ")}[/color][/size][/b]\n\n'
                f'[b]General Information: [/b][font=monospace]\n\n'
                f'{self.media_info}'
                f'{f"{enter}{enter}" + self.parsed_encoder_log if self.parsed_encoder_log else ""}\n'
                f'[/font][/quote]\n'
                f'{before_screen_shot}\n'
                f'{f"{enter}".join([f"{uploaded}" for uploaded in self._screenshots])}')

    def generate_filename(self):
        filename = ""
        if self._ptgen.get("chinese_title"):
            filename += f"{self._ptgen.get('chinese_title').strip()}."

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
            filename += f"{aka_name}."
        else:
            chinese_title = p.get_pinyin(self._ptgen.get('chinese_title').strip()).replace('-', '.').title()
            filename += f"{chinese_title}."

        season = ""
        if self.custom_season:
            season = f"S0{self.custom_season.strip()}" \
                if len(self.custom_season.strip()) == 1 \
                else f"S{self.custom_season.strip()}"
        elif self._ptgen.get("current_season"):
            season = f"S0{self._ptgen.get('current_season').strip()}" \
                if len(self._ptgen.get('current_season').strip()) == 1 \
                else f"S{self._ptgen.get('current_season').strip()}"

        episode = ""
        if self.custom_episode:
            episode = f"E0{self.custom_episode.strip()}" \
                if len(self.custom_episode.strip()) == 1 \
                else f"E{self.custom_episode.strip()}"

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

        filename = re.sub(chinese_mark_re, '', filename)
        with open(f"{self._main_file.parent}/filename.txt", "w", encoding="utf-8") as f:
            f.write(filename)
        exit(0)

    @property
    def video_type(self):
        if "webdl" in self.folder.name.lower() or "web-dl" in self.folder.name.lower():
            return "web-dl"
        elif "remux" in self.folder.name.lower():
            return "remux"
        elif "hdtv" in self.folder.name.lower():
            return "hdtv"
        elif any(e in self.folder.name.lower() for e in ("x264", "x265")):
            return "encode"
        elif "bluray" in self.folder.name.lower() and not any(
            e in self.folder.name.lower() for e in ("x264", "x265")
        ):
            return "bluray"
        elif "uhd" in self.folder.name.lower():
            return "uhdbluray"
        for track in self._mediainfo.tracks:
            if track.track_type == "Video":
                if track.encoding_settings:
                    return "encode"
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
