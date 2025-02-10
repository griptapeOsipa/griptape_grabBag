"""
Preview Video Processing Tools for Griptape

This script provides an early implementation of video processing utilities 
designed for use within the Griptape framework. It includes tools for extracting 
video metadata, segmenting and splitting video, adding timecode overlays, 
and extracting audio using FFmpeg.

While this is a functional prototype, the goal is to evolve it into a fully 
fledged Griptape extension with improved structure, modularity, and integration.

You'll need to have FFmpeg and FFprobe installed for these tools to function properly.

Example usage:

import random
from griptape.video_tools.tools.ffmpeg_tool.tool import (
    VideoSegmentCalculatorTool, VideoTimecodeOverlayTool,
    VideoSplitterTool, VideoInfoTool, AudioExtractorTool
)
from griptape.video_tools.utils.env_utils import get_env_var
from griptape.tasks import PromptTask
from dotenv import load_dotenv

load_dotenv()
api_key = get_env_var("OPENAI_API_KEY")

TEST_MOVIE = "examples/video_tools/tools/ffmpeg_tool/media/20240704_FosterCityCA.mp4"

prompts = [
    f'With {TEST_MOVIE}, cut in three, use the suffix "thirds"',
    f'With {TEST_MOVIE}, give me all but the last 4 seconds',
    f'With {TEST_MOVIE}, cut it into 2-second chunks',
    f'With {TEST_MOVIE}, give me just seconds 6-10',
    f'With {TEST_MOVIE}, give me the first 5 seconds',
    f'With {TEST_MOVIE}, extract the audio',
    f'With {TEST_MOVIE}, cut it in half',
    f'With {TEST_MOVIE}, add timecode',
]

random.shuffle(prompts)

task = PromptTask(
    prompts[0],
    tools=[
        VideoSegmentCalculatorTool(),
        VideoTimecodeOverlayTool(),
        AudioExtractorTool(),
        VideoSplitterTool(),
        VideoInfoTool(),
    ]
)

result = task.run()
print(result)
"""


import subprocess
import os

from schema import Literal, Optional, Schema, Or
from typing import Union

from griptape.utils.decorators import activity
from griptape.artifacts import ErrorArtifact, TextArtifact, InfoArtifact
from griptape.tools import BaseTool


class VideoInfoTool(BaseTool):
    @activity(
        config={
            "description": "Extracts video metadata (e.g., duration) using ffprobe.",
            "schema": Schema(
                {
                    Literal(
                        "mov",
                        "Path to the video file",
                    ): str,
                }
            ),
        }
    )
    def get_video_info(self, params: dict) -> Union[InfoArtifact, ErrorArtifact]:
        """
        Returns:
        A dictionary with video information (duration in seconds).
        """
        mov = params[ 'values' ][ 'mov' ]

        if not os.path.exists(mov):
            return ErrorArtifact( f"File not found: {mov}" )

        cmd =   [
                    "ffprobe", "-v", "error", "-show_entries", "format=duration",
                    "-of", "default=noprint_wrappers=1:nokey=1",
                    mov,
                ]
        try:
            result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            duration = float(result.stdout.strip())
            return InfoArtifact({"duration": duration})

        except Exception as e:
            return ErrorArtifact( f"Error retrieving video duration: {e}" )


class VideoSegmentCalculatorTool(BaseTool):
    @activity(
        config={
            "description": "Calculates segment start and end times for video splitting.",
            "schema": Schema(
                {
                    Literal(
                        "duration",
                        "The length of the original video clip, provided by VideoInfoTool",
                    ): float,
                    Literal(
                        "method",
                        'How to split the video.',# Only allowed values are "equal" or "duration"',
                    ): Or( "equal", "duration" ),# pyright: ignore
                    Literal(
                        "value",
                        "Value associated with the method",
                    ): Or( int, float ),
                }
            ),
        }
    )
    def calculate_segments(self, params: dict) -> Union[InfoArtifact, ErrorArtifact]:
        '''
        Returns:
        A list of segment start and end times.
        '''
        duration = params[ 'values' ][ 'duration' ]
        method   = params[ 'values' ][ 'method'   ]
        value    = params[ 'values' ][ 'value'    ]
        
        if not duration or not method or not value:
            return ErrorArtifact("Missing required inputs: 'duration', 'method', or 'value'.")

        segments = []
        if method == "equal":
            num_segments = value
            segment_duration = duration / num_segments
            for i in range(num_segments):
                start = i * segment_duration
                end   = min( start + segment_duration, duration )
                segments.append( { "start": start, "end": end } )
        elif method == "duration":
            segment_duration = value
            start = 0
            while start < duration:
                end = min( start + segment_duration, duration )
                segments.append( { "start": start, "end": end } )
                start += segment_duration
        else:
            return ErrorArtifact( f"Invalid method: {method}. Use 'equal' or 'duration'." )

        return InfoArtifact( { "segments": segments } )


class VideoSplitterTool(BaseTool):
    @activity(
        config={
            "description": "Can create subclips of video files using start and end times",
            "schema": Schema(
                {
                    Literal(
                        "mov",
                        "Path to the video file",
                    ): str,
                    Literal(
                        "segments",
                        "List of start and end times (list of dicts with 'start' and 'end')",
                    ): [ { 'start': Or( int, float ),'end': Or( int, float ) } ],
                    Literal(
                        "output_name",
                        'Optional mid-suffix for output files, default will simply be "segment"',
                    ): Optional(str),
                }
            ),
        }
    )
    def split_video(self, params: dict) -> Union[TextArtifact, ErrorArtifact]:
        '''
        Returns:
        A success message with the list of generated file paths.
        '''
        mov      = params[ 'values' ][ 'mov'      ]
        segments = params[ 'values' ][ 'segments' ]
        out_name = params.get('values', {}).get( 'output_name', 'segment' )

        if not os.path.exists(mov):
            return ErrorArtifact( f"Error: File not found: {mov}" )
        if not segments:
            return ErrorArtifact( f"Error: No segments provided." )

        # Parse the file path and name
        pth, ext = os.path.splitext( mov )
        pth, nam = os.path.split(    pth )

        output_files = []
        for i, segment in enumerate(segments, 1):
            start = segment[ "start" ]
            end   = segment[   "end" ]
            sfx   = f'_{i:02}{ext}' if len(segments) > 1 else ext
            out   = os.path.join( pth, f"{nam}_{out_name}{sfx}" )

            if os.path.exists(out):
                print(f'Deleting existing "{out}"')
                os.remove(out)

            cmd = [
                "ffmpeg", "-ss", str(start),
                "-i", mov,  "-t", str(end-start), #str(end - (start-0.5)),
                "-c:v", "libx264", "-avoid_negative_ts", "1",
                out,
            ]
            subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            output_files.append(out)

        return TextArtifact( f"Clips successfully created: {', '.join(output_files)}" )


class AudioExtractorTool(BaseTool):
    @activity(
        config={
            "description": "Extracts audio from a video file using ffmpeg.",
            "schema": Schema(
                {
                    Literal(
                        "mov",
                        "Path to the video file",
                    ): str,
                    Literal(
                        "output_name",
                        'Optional output file name (default is the video name with ".mp3" extension)',
                    ): Optional(str),
                }
            ),
        }
    )
    def extract_audio(self, params: dict) -> Union[TextArtifact, ErrorArtifact]:
        """
        Extracts audio from the given video file.

        Returns:
        A success message with the path to the extracted audio file.
        """
        mov = params["values"]["mov"]
        out_name = params.get("values", {}).get("output_name")

        if not os.path.exists(mov):
            return ErrorArtifact(f"Error: File not found: {mov}")

        # Determine the output audio file name
        stb, ext = os.path.splitext( mov )
        pth, nam = os.path.split( stb )

        #Fuzzy logic and UX can create a cycle of extentions here - cleaning up
        while True:
            if out_name.lower().endswith('.mp3'):
                print(f'Naming cleanup on: {out_name}')
                out_name, ext = os.path.splitext( out_name )
            else:
                break

        if not out_name:
            out = os.path.join( pth, f"{nam}.mp3" )
        else:
            out = os.path.join( pth, f"{out_name}.mp3" )

        # Ensure the output file does not already exist
        if os.path.exists(out):
            print(f'Deleting existing "{out}"')
            os.remove(out)

        # Use ffmpeg to extract audio
        cmd = [
            "ffmpeg", "-i", mov,
            "-q:a", "0", "-map", "a",  # High-quality audio extraction
            out,
        ]

        try:
            subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
            return TextArtifact(f"Audio successfully extracted: {out}")
        except subprocess.CalledProcessError as e:
            return ErrorArtifact(f"Error extracting audio: {e}")


class VideoTimecodeOverlayTool(BaseTool):
    @activity(
        config={
            "description": "Adds a timecode overlay to the video using FFmpeg.  Always perform this operation last",
            "schema": Schema(
                {
                    Literal(
                        "mov",
                        "Path to the video file",
                    ): str,
                    Literal(
                        "output_name",
                        "Optional output name suffix for the timecoded video (default is '_timecoded')",
                    ): Optional(str),
                }
            ),
        }
    )
    def add_timecode_overlay(self, params: dict) -> Union[TextArtifact, ErrorArtifact]:
        """
        Adds a timecode overlay to the video.

        Returns:
        A success message with the path to the timecoded video file.
        """
        mov = params["values"]["mov"]
        out_name = params.get("values", {}).get("output_name", "_timecoded")

        if not os.path.exists(mov):
            return ErrorArtifact(f"Error: File not found: {mov}")

        # Parse the file path and name
        pth, ext = os.path.splitext(mov)
        pth, nam = os.path.split(pth)

        # Define the output video file with a "_timecoded" suffix
        out = os.path.join(pth, f"{nam}{out_name}{ext}")

        # Ensure the output file does not already exist
        if os.path.exists(out):
            print(f'Deleting existing "{out}"')
            os.remove(out)

        # Use FFmpeg to add the timecode overlay
        timecode_data   = "drawtext=text='%{pts\\:hms}'"
        timecode_size   = "fontsize='48*(w/1920)'"
        timecode_color  = "fontcolor=white"
        timecode_offset = "x=(w-text_w-text_h*2):y=(h-text_h-text_h*2)"
        cmd = [
            "ffmpeg", "-i", mov, "-vf",
            f"{timecode_data}:{timecode_size}:{timecode_color}:{timecode_offset}",
            out
        ]
        try:
            subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
            return TextArtifact(f"Timecode overlay added: {out}")
        except subprocess.CalledProcessError as e:
            return ErrorArtifact(f"Error adding timecode overlay: {e}")
