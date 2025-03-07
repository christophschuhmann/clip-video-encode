from srcs.rename_and_move import main as rename_and_move_main
from srcs.segment_averaging import main as segment_averaging_main
from srcs.move_and_group import main as move_and_group_main
from srcs.save_to_webdataset import main as save_to_webdataset_main
from srcs.whisper import process_audio_files
from srcs.successor_segmentation import SegmentSuccessorAnalyzer, run_analysis
from srcs.fold_seams import main as fold_seams_main
from srcs.convert_types import main as convert_types_main
import os
import shutil
from srcs.pipeline import read_config, string_to_bool

def remove_incomplete_video_directories(completedirectory):
    required_dirs = ['keyframe_audio_clips', 'keyframeembeddings', 'keyframes', 'keyframevideos', 'originalvideos']
    base_dir = completedirectory['completedatasets']
    for video_dir in os.listdir(base_dir):
        video_path = os.path.join(base_dir, video_dir)
        if not os.path.isdir(video_path):
            continue
        missing_or_empty = any(not os.path.exists(os.path.join(video_path, req_dir)) or not os.listdir(os.path.join(video_path, req_dir)) for req_dir in required_dirs)
        if missing_or_empty:
            shutil.rmtree(video_path)
            print(f"Removed incomplete or empty directory: {video_path}")

def run_all_scripts():
    config_params = read_config(section="config_params")
    completedirectory = read_config(section="evaluations")
    segment_video = string_to_bool(config_params.get("segment_video", "False"))
    segment_audio = string_to_bool(config_params.get("segment_audio", "True"))
    compute_embeddings = string_to_bool(config_params.get("compute_embeddings", "False"))
    specific_videos_str = config_params.get("specific_videos", "")
    specific_videos = [int(x.strip()) for x in specific_videos_str.strip('[]').split(',')] if specific_videos_str and specific_videos_str != "None" else None
    try:
        rename_and_move_main()
        run_analysis(SegmentSuccessorAnalyzer)
        fold_seams_main(segment_video, segment_audio, specific_videos)
        if compute_embeddings:
            segment_averaging_main()
        move_and_group_main()
        remove_incomplete_video_directories(completedirectory)
        process_audio_files()
        convert_types_main()
        save_to_webdataset_main()
    except Exception as e:
        print(f"An error occurred in the pipeline: {e}")

def initialize_and_run():
    try:
        run_all_scripts()
    except FileNotFoundError as e:
        print(f"File or directory not found: {e}")
    except Exception as e:
        print(f"Unexpected error occurred: {e}")

if __name__ == "__main__":
    initialize_and_run()