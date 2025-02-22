import argparse
import os
import torch

import numpy as np
import torch
import random

import re 
import yaml

import shutil
import warnings
from copy import deepcopy

from datetime import datetime


class Namespace(object):
    def __init__(self, somedict):
        for key, value in somedict.items():
            assert isinstance(key, str) and re.match("[A-Za-z_-]", key)
            if isinstance(value, dict):
                self.__dict__[key] = Namespace(value)
            else:
                self.__dict__[key] = value
    
    def __getattr__(self, attribute):

        raise AttributeError(f"Can not find {attribute} in namespace. Please write {attribute} in your config file(xxx.yaml)!")

def update_args(args, key, value):
    args_update = deepcopy(args)
    setattr(args_update, key, value)
    return args_update

def init_args(args_dict):
    """Init argparse from dictionary."""
    parser = argparse.ArgumentParser()
    args = parser.parse_args([])
    args.__dict__ = args_dict
    return args

def set_deterministic(seed):
    # seed by default is None 
    if seed is not None:
        print(f"Deterministic with seed = {seed}")
        random.seed(seed) 
        np.random.seed(seed) 
        torch.manual_seed(seed)
        torch.cuda.manual_seed(seed)
        torch.backends.cudnn.deterministic = True 
        torch.backends.cudnn.benchmark = False 

def get_args():
    parser = argparse.ArgumentParser()
    parser.add_argument('-c', '--config-file', required=True, type=str, help="xxx.yaml")
    parser.add_argument('--debug', action='store_true')
    parser.add_argument('--debug_subset_size', type=int, default=8)
    parser.add_argument('--download', action='store_true', help="if can't find dataset, download from web")
    parser.add_argument('--data_dir', type=str, default=os.getenv('DATA'))
    parser.add_argument('--log_dir', type=str, default=os.getenv('LOG'))
    parser.add_argument('--ckpt_dir', type=str, default=os.getenv('CHECKPOINT'))
    parser.add_argument('--ckpt_dir_1', type=str, default=os.getenv('CHECKPOINT'))
    parser.add_argument('--device', type=str, default='cuda'  if torch.cuda.is_available() else 'cpu')
    parser.add_argument('--eval_from', type=str, default=None)
    parser.add_argument('--hide_progress', action='store_true')
    parser.add_argument('--cl_default', action='store_true')
    parser.add_argument('--last', action='store_true')
    parser.add_argument('--debug_lpft', action='store_true')
    parser.add_argument('--lpft', action='store_true')
    parser.add_argument('--save_as_orig', action='store_true')
    parser.add_argument('--validation', action='store_true',
                        help='Test on the validation set')
    parser.add_argument('--ood_eval', action='store_true',
                        help='Test on the OOD set')
    args = parser.parse_args()


    with open(args.config_file, 'r') as f:
        for key, value in Namespace(yaml.load(f, Loader=yaml.FullLoader)).__dict__.items():
            vars(args)[key] = value

    if args.debug:
        if args.train: 
            args.train.batch_size = 256
            args.train.num_epochs = 2
            args.train.stop_at_epoch = 2
            if args.lpft:
                assert args.debug_lpft, "cover this case"
                args.train.num_lp_epochs = 1
        if args.eval: 
            args.eval.batch_size = 2
            args.eval.num_epochs = 1 # train only one epoch
        args.dataset.num_workers = 0


    assert not None in [args.log_dir, args.data_dir, args.ckpt_dir, args.name]

    args.log_dir = os.path.join(args.log_dir, 'in-progress_'+datetime.now().strftime('%m%d%H%M%S_')+args.name)

    os.makedirs(args.log_dir, exist_ok=False)
    print(f'creating file {args.log_dir}')
    os.makedirs(args.ckpt_dir, exist_ok=True)

    shutil.copy2(args.config_file, args.log_dir)
    set_deterministic(args.seed)


    vars(args)['aug_kwargs'] = {
        'name':args.model.name,
        'image_size': args.dataset.image_size,
        'cl_default': args.cl_default,
        'scale': 0.2,
    }
    vars(args)['dataset_kwargs'] = {
        # 'name':args.model.name,
        # 'image_size': args.dataset.image_size,
        'dataset':args.dataset.name,
        'data_dir': args.data_dir,
        'download':args.download,
        'debug_subset_size': args.debug_subset_size if args.debug else None,
        # 'drop_last': True,
        # 'pin_memory': True,
        # 'num_workers': args.dataset.num_workers,
    }
    vars(args)['dataloader_kwargs'] = {
        'drop_last': True,
        'pin_memory': True,
        'num_workers': args.dataset.num_workers,
    }

    return args
