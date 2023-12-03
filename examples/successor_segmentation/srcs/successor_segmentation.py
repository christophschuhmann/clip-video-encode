import glob
import json
import os
import cv2
import logging
import numpy as np
from sklearn.preprocessing import normalize
import configparser
from matplotlib import pyplot as plt
from matplotlib.lines import Line2D
from typing import List, Tuple, Optional, Dict, Union
import numpy as np
from PIL import Image
from imagehash import phash
from matplotlib.patches import Rectangle

from srcs.segment_processing import get_segmented_and_filtered_frames, calculate_successor_distance, check_for_new_segment
import srcs.load_data as ld 

class SegmentSuccessorAnalyzer:
    def __init__(self, total_duration: float, embedding_values: np.ndarray, thresholds: Dict[str, Optional[float]],
                 max_segment_duration: Optional[int] = None) -> None:
        # Validate types
        if not isinstance(total_duration, float):
            raise TypeError("total_duration must be a float.")
        if not isinstance(embedding_values, np.ndarray):
            raise TypeError("embedding_values must be a numpy array.")
        # Read and store thresholds from config
        self.thresholds = self.read_thresholds_config()
        self.embedding_values = embedding_values
        self.total_duration = total_duration
        self.max_segment_duration = int(self.thresholds['max_duration']) if max_segment_duration is not None else None

    @staticmethod
    def read_thresholds_config(section: str = 'thresholds') -> dict:
        params = ld.read_config(section=section)
        return {key: None if params.get(key) in [None, 'None'] else float(params.get(key)) 
                for key in ['successor_value', 'phash_threshold']}

    def run(self, video_files: List[str], thresholds: Dict[str, Optional[float]], keyframe_files: List[str], save_dir: str) -> Tuple[List[np.ndarray], List[float]]:
        # Use stored thresholds if none are provided
        thresholds = thresholds or self.thresholds
        frame_embedding_pairs, timestamps = get_segmented_and_filtered_frames(video_files, keyframe_files,self.embedding_values, thresholds)
        # Check for edge cases
        if len(frame_embedding_pairs) < 2:
            print(f"Insufficient number of frame embeddings for {video_files}. Skipping analysis.")
            return [], []
        try:
            temporal_embeddings = np.array([emb for _, emb in frame_embedding_pairs])
            distances = np.linalg.norm(temporal_embeddings[1:] - temporal_embeddings[:-1], axis=1)
        except AxisError as e:
            print(f"An AxisError occurred while processing {video_files}: {e}. Skipping analysis.")
            return [], []
        successor_distance = calculate_successor_distance(self.embedding_values)
        initial_new_segments = check_for_new_segment(distances, successor_distance, thresholds)
        new_segments = self.calculate_new_segments(initial_new_segments, timestamps)
        self.save_keyframes(frame_embedding_pairs, new_segments, distances, successor_distance, timestamps, save_dir)

    def calculate_new_segments(self, initial_new_segments, timestamps):
        if self.max_segment_duration is None:
            return initial_new_segments
        new_segments = [initial_new_segments[0]]
        last_timestamp = timestamps[new_segments[0]]  # Initialize with the timestamp of the first segment
        for i in range(0, len(initial_new_segments)):
            if i == 0:
                # Special handling for the first element
                new_segments.append(initial_new_segments[i])
                last_timestamp = timestamps[initial_new_segments[i]]
                continue
            current_timestamp = timestamps[initial_new_segments[i]]
            if (current_timestamp - last_timestamp) > self.max_segment_duration:
                acceptable_frame = self.find_acceptable_frame(initial_new_segments[i-1:i+1], timestamps, last_timestamp)
                if acceptable_frame not in new_segments:
                    new_segments.append(acceptable_frame)
                    last_timestamp = timestamps[acceptable_frame]
            if initial_new_segments[i] not in new_segments:
                new_segments.append(initial_new_segments[i])
                last_timestamp = timestamps[initial_new_segments[i]]  # Update last_timestamp
        return new_segments

    def find_acceptable_frame(self, intervening_frames, timestamps, last_timestamp):
        if intervening_frames[1] > len(timestamps):
            print(f"Index out of bounds: {intervening_frames[1]} for timestamps of length {len(timestamps)}")
        for j in range(intervening_frames[0], min(intervening_frames[1], len(timestamps))):
            if (timestamps[j] - last_timestamp) <= self.max_segment_duration:
                return j
        return intervening_frames[0]

    def save_keyframes(self, frame_embedding_pairs, new_segments, distances, successor_distance, timestamps, save_dir):
        if len(frame_embedding_pairs) != len(timestamps):
            print("Mismatch in the number of frames and timestamps. Exiting save_keyframes.")
            return

        num_frames = len(frame_embedding_pairs)
        num_cols = 4
        num_rows = int(np.ceil(num_frames / num_cols))
        if num_rows <= 0 or num_cols <= 0:
            print("Invalid grid dimensions. Skipping grid plotting.")
            return
        fig, axes = plt.subplots(num_rows, num_cols, figsize=(4 * num_cols, 4 * num_rows))
        if num_frames == 1:
            axes = np.array([[axes]])
        flat_axes = axes.flatten()
        keyframe_data = {}
        segment_counter = 0 # Start counting from 1
        for i, ax in enumerate(flat_axes[:num_frames]):
            frame, _ = frame_embedding_pairs[i]
            if is_clear_image(frame):
                ax.imshow(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
                if i in new_segments:
                    segment_counter += 1
                annotate_plot(ax, idx=i, successor_sim=successor_distance, distances=distances,
                              global_frame_start_idx=0, window_idx=i,
                              segment_label=f"Segment {segment_counter}", timestamp=timestamps[i])
                individual_keyframe_filename = f'keyframe_{i}_timestamp_{timestamps[i]:.2f}.png'
                individual_keyframe_path = os.path.join(save_dir, individual_keyframe_filename)
                cv2.imwrite(individual_keyframe_path, frame)
                keyframe_data[i] = {
                    'index': i,
                    'time_frame': timestamps[i],
                    'filename': individual_keyframe_filename
                }

        for ax in flat_axes[num_frames:]:
            ax.axis('off')
        plt.tight_layout()
        plt_path = os.path.join(save_dir, 'keyframes_grid.png')
        plt.savefig(plt_path)
        json_path = os.path.join(save_dir, 'keyframe_data.json')
        with open(json_path, 'w') as f:
            json.dump(keyframe_data, f)

def remove_whitespace(frame):
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    gray = 255*(gray < 128).astype(np.uint8)
    coords = cv2.findNonZero(gray)
    x, y, w, h = cv2.boundingRect(coords)
    return frame[y:y+h, x:x+w]

def annotate_plot(ax, idx, successor_sim, distances, global_frame_start_idx, window_idx, segment_label, timestamp=None):
    title_elements = [
        f"{segment_label}",
        f"Successor Value: {successor_sim[idx]:.2f}" if idx < len(successor_sim) else "No Successor Value"]
    if timestamp is not None:
        title_elements.append(f"Timestamp: {timestamp}")
    if idx >= len(successor_sim):
        print(f"Index out of bounds in annotate_plot: {idx} for successor_sim of length {len(successor_sim)}")
    ax.set_title("\n".join(title_elements), fontsize=8, pad=6)
    legend_elements = [Line2D([0], [0], marker='o', color='w', label=f"Frame Index {global_frame_start_idx + idx}", markersize=8)]
    ax.legend(handles=legend_elements, fontsize=6)
    
def is_clear_image(frame, lower_bound=10, upper_bound=245):
    gray_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    mean_intensity = np.mean(gray_frame)
    return lower_bound < mean_intensity < upper_bound

def run_analysis(analyzer_class, specific_videos=None):
    thresholds = SegmentSuccessorAnalyzer.read_thresholds_config()  
    params = ld.read_config(section="directory")
    video_ids = ld.get_all_video_ids(params['originalframes'])
    if specific_videos is not None:
        video_ids = [vid for vid in video_ids if vid in specific_videos]
    for video in video_ids:
        try:
            keyframe_embedding_files = ld.load_keyframe_embedding_files(video, params)
            embedding_values = ld.load_embedding_values(keyframe_embedding_files)
        except ValueError as e:
            if str(e) == "No embedding files provided.":
                logging.warning(f"Skipping video {video} due to missing embedding files.")
                continue
            elif str(e) == "Failed to load any arrays from embedding files.":
                logging.warning(f"Skipping video {video} due to failure in loading arrays.")
                continue
            else:
                raise
        video_files = ld.load_video_files(video, params)
        if not video_files:
            print(f"No video files found for video: {video}. Skipping analysis.")
            continue
        key_video_files = ld.load_key_video_files(video, params)
        total_duration = ld.get_video_duration(video_files)
        keyframe_outputs = params['keyframe_outputs']
        save_dir = f"{keyframe_outputs}/{video}"
        os.makedirs(save_dir, exist_ok=True)
        analyzer = analyzer_class(total_duration, embedding_values, thresholds)
        analyzer.run(video_files, thresholds, key_video_files, save_dir)