import configparser
import shutil
import sys
import os
import pickle
import json
import re
import glob
import subprocess
import traceback
import re

from evaluations.pipeline_eval import modify_hook_file

import pandas as pd
import numpy as np
try:
    import laion_clap
    import open_clip
    import tensorflow as tf
    import torch
    from PIL import Image
except ImportError as e:
    # Extract the traceback
    tb = traceback.format_exc()
    # Use regex to find the file path of hook.py
    match = re.search(r'File "(.*?/laion_clap/hook\.py)", line \d+, in', tb)
    if match:
        hook_file_path = match.group(1)
        print(f"Path to hook.py: {hook_file_path}")
        modify_hook_file(hook_file_path)
        import laion_clap
        import open_clip
        import tensorflow as tf
        import torch
        from PIL import Image
    else:
        print("Could not find the path to hook.py in the ImportError traceback.")

config_path='./clip-video-encode/examples/successor_segmentation/config.ini'

def read_config(section, config_path=config_path):
    if not os.path.exists(config_path):
        raise FileNotFoundError(f"Configuration file {config_path} not found.")
    config = configparser.ConfigParser()
    config.read(config_path)
    if section not in config.sections():
        raise KeyError(f"Section {section} not found in configuration file.")
    return {key: config[section][key] for key in config[section]}
    
def load_key_image_files(vid, params):
    pattern = os.path.join(params['completedatasets'], str(vid), "keyframes", "*.png")
    return iter(sorted(glob.glob(pattern)))

def load_key_audio_files(vid, params):
    pattern = os.path.join(params['completedatasets'], str(vid), "keyframe_audio_clips", "whisper_audio_segments", "*.flac")
    return iter(sorted(glob.glob(pattern)))

def get_all_video_ids(directory):
    return iter([int(os.path.basename(f)) for f in glob.glob(os.path.join(directory, '*'))])

def tensor_to_array(tensor):
    return tensor.cpu().numpy()

def generate_embeddings(tokenizer, model_clip, prompts, file_name):
    if not os.path.exists(file_name + '.npy'):
        device = 'cuda' if torch.cuda.is_available() else 'cpu'
        text = tokenizer(prompts).to(device)
        with torch.no_grad():
            text_features = model_clip.encode_text(text)
            text_features /= text_features.norm(dim=-1, keepdim=True)
        text_features = tensor_to_array(text_features) 
        np.save(file_name, text_features)
    else:
        text_features = np.load(file_name + '.npy')
    return text_features

def remove_duplicate_extension(filename):
    parts = filename.split('.')
    if len(parts) > 2 and parts[-1] == parts[-2]:
        return '.'.join(parts[:-1])
    return filename

def display_image_from_file(image_path):
    img = Image.open(image_path)
    display(img)

def print_top_n(probs, labels):
    top_n_indices = np.argsort(probs)[::-1][:5]
    for i in top_n_indices:
        print(f"{labels[i]}: {probs[i]:.4f}")

def normalize_scores(scores):
    mean = np.mean(scores, axis=1)
    std = np.std(scores, axis=1)
    normalized_scores = (scores - mean) / std
    return normalized_scores

def softmax(x):
    e_x = np.exp(x - np.max(x))
    return e_x / e_x.sum(axis=-1, keepdims=True)

def sort_and_store_scores(probabilities, labels):
    min_length = min(len(probabilities), len(labels))
    scores = {labels[i]: float(probabilities[i]) for i in range(min_length)}
    sorted_scores = dict(sorted(scores.items(), key=lambda item: item[1], reverse=True))
    return sorted_scores

def process_keyframe_audio_pairs(faces_dir, audio_dir, output_dir):
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
    keyframe_filenames = [f for f in os.listdir(faces_dir) if f.endswith('.png')]
    for keyframe_filename in keyframe_filenames:
        segment_match = re.search(r'keyframe_(\d+)_', keyframe_filename)
        video_match = faces_dir.split('/')[-1]
        video_match = re.search(r'(\d+)', video_match)
        video_idx = int(video_match.group(1))
        if segment_match:
            segment_idx = int(segment_match.group(1))
            audio_filename = f"segment_{segment_idx}__keyframe.flac"
            text_filename = f"video_{video_idx}_keyframe_audio_clip_{segment_idx}.txt"
            audio_path = os.path.join(audio_dir, audio_filename)
            text_path = os.path.join(audio_dir, text_filename)
            image_path = os.path.join(faces_dir, keyframe_filename)
            if os.path.isfile(audio_path):
                output_audio_path = os.path.join(output_dir, audio_filename)
                shutil.copy(audio_path, output_audio_path)
                print(f"Copied {audio_path} to {output_audio_path}")
            if os.path.isfile(text_path):
                output_text_path = os.path.join(output_dir, text_filename)
                shutil.copy(text_path, output_text_path)
                print(f"Copied {text_path} to {output_text_path}")
            if os.path.isfile(image_path):
                output_image_path = os.path.join(output_dir, keyframe_filename)
                shutil.copy(image_path, output_image_path)
                print(f"Copied {image_path} to {output_image_path}")
        else:
            print(f"No digits found in filename: {keyframe_filename}")
            
def format_labels(labels, key):
    return [label.strip() for label in labels[key].replace('\\\n', '').split(',')]
    
def get_model_device(model):
    return next(model.parameters()).device

def model_clip(config_path=config_path):
    model_config = read_config('evaluations', config_path)
    model_name = model_config['model_clip']
    model_clip, preprocess_train, preprocess_val = open_clip.create_model_and_transforms(model_name)
    tokenizer = open_clip.get_tokenizer(model_name)
    if torch.cuda.is_available():
        if torch.cuda.device_count() > 1:
            model_clip = torch.nn.DataParallel(model_clip)
        model_clip = model_clip.to('cuda')
    return model_clip, preprocess_train, preprocess_val, tokenizer

def model_clap(config_path=config_path):
    model_config = read_config('evaluations', config_path)
    if not os.path.isfile(model_config['model_clap_checkpoint'].split('/')[-1]):
        subprocess.run(['wget', model_config['model_clap_checkpoint']])
    model_clap = laion_clap.CLAP_Module(enable_fusion=False, amodel=model_config['model_clap'])
    checkpoint = torch.load(model_config['model_clap_checkpoint'].split('/')[-1], map_location='cpu')
    model_clap.load_state_dict(checkpoint, strict=False)
    if torch.cuda.is_available():
        if torch.cuda.device_count() > 1:
            model_clap = torch.nn.DataParallel(model_clap)
        model_clap = model_clap.to('cuda')
    return model_clap

def prepare_audio_labels():
    if not os.path.exists('clap-audioset-probe/clap-probe.pkl') or not os.path.exists('clap-audioset-probe/clap-probe.csv'):
        print("Required files not found. Cloning repository...")
        subprocess.run(["git", "clone", "https://github.com/MichaelALong/clap-audioset-probe"])
    # Load the model
    with open('clap-audioset-probe/clap-probe.pkl', 'rb') as f:
        multioutput_model = pickle.load(f)
    # Load the metrics
    dfmetrics = pd.read_csv("clap-audioset-probe/clap-probe.csv")
    dfmetrics = dfmetrics.sort_values("model_order")
    model_order_to_group_name = pd.Series(dfmetrics.group_name.values, index=dfmetrics.model_order).to_dict()
    return multioutput_model, model_order_to_group_name, dfmetrics
    
def get_audio_embeddings(audio_path, model_clap):
    audio_files = sorted([f for f in glob.glob(audio_path + '/**/*.mp3', recursive=True) if re.search(r'segment_\d+__keyframe(_vocals)?\.mp3$', f)])
    embeddings = []
    for input_file in audio_files:
        audio_embed = model_clap.get_audio_embedding_from_filelist([input_file], use_tensor=True)
        device = 'cuda' if torch.cuda.is_available() else 'cpu'
        audio_embed = audio_embed.to(device)
        normalized_embed = normalize_scores(audio_embed.detach().reshape(1, -1).cpu().numpy())
        embeddings.append(normalized_embed)
    return audio_files, np.vstack(embeddings)

def get_embeddings(model_clip, tokenizer, config_path=config_path):
    evals = read_config('evaluations')
    labels = read_config('labels')
    
    emotions = format_labels(labels, 'emotions')
    check_if_person_list = format_labels(labels, 'checkifperson')
    number_of_faces_list = format_labels(labels, 'numberoffaces')
    engagement_labels_list = format_labels(labels, 'engagementlabels')
    orientation_labels_list = format_labels(labels, 'orientationlabels')
    check_type_person_list = format_labels(labels, 'checktypeperson')
    valence_list = format_labels(labels, 'valence')

    text_features = generate_embeddings(tokenizer, model_clip, emotions, f"{evals['embeddings']}/text_features.npy")
    text_features_if_person = generate_embeddings(tokenizer, model_clip, check_if_person_list, f"{evals['embeddings']}/text_features_if_person.npy")
    text_features_type_person = generate_embeddings(tokenizer, model_clip, check_type_person_list, f"{evals['embeddings']}/text_features_type_person.npy")
    text_features_if_number_of_faces = generate_embeddings(tokenizer, model_clip, number_of_faces_list, f"{evals['embeddings']}/text_features_number_of_faces.npy")
    text_features_orientation = generate_embeddings(tokenizer, model_clip, orientation_labels_list, f"{evals['embeddings']}/text_features_orientation.npy")
    text_features_if_engaged = generate_embeddings(tokenizer, model_clip, engagement_labels_list, f"{evals['embeddings']}/text_features_if_engaged.npy")
    text_features_valence = generate_embeddings(tokenizer, model_clip, valence_list, f"{evals['embeddings']}/text_valence.npy")