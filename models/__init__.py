import os
import importlib
from .simsiam import SimSiam
from .barlowtwins import BarlowTwins
from torchvision.models import resnet50, resnet18, resnet34, resnet101, resnet152
import torch
from .backbones import resnet18


def get_num_params(model, is_trainable = None):
    """Get number of parameters of the model, specified by 'None': all parameters;
    True: trainable parameters; False: non-trainable parameters.
    """
    num_params = 0
    for param in list(model.parameters()):
        nn=1
        if is_trainable is None \
            or (is_trainable is True and param.requires_grad is True) \
            or (is_trainable is False and param.requires_grad is False):
            for s in list(param.size()):
                nn = nn * s
            num_params += nn
    return num_params

def get_backbone(backbone, dataset, castrate=True):
    backbone = eval(f"{backbone}()")
    if dataset == 'seq-cifar100':
        backbone.n_classes = 100
    elif dataset == 'seq-cifar10':
        backbone.n_classes = 10
    backbone.output_dim = backbone.fc.in_features
    if not castrate:
        backbone.fc = torch.nn.Identity()

    return backbone


def get_all_models():
    return [model.split('.')[0] for model in os.listdir('/sailhome/msun415/UCL/models')
            if not model.find('__') > -1 and 'py' in model]

def get_model(args, device, len_train_loader, transform):
    loss = torch.nn.CrossEntropyLoss()
    if args.model.name == 'simsiam':
        backbone =  SimSiam(get_backbone(args.model.backbone, args.dataset.name, args.cl_default)).to(device)
        for class_ in [resnet18, resnet34, resnet50, resnet101, resnet152]:            
            backbone_ = class_()
            print(f"{class_.__name__} has {get_num_params(backbone_)} params")
        if args.model.proj_layers is not None:
            backbone.projector.set_layers(args.model.proj_layers)
    elif args.model.name == 'barlowtwins':
        backbone = BarlowTwins(get_backbone(args.model.backbone, args.dataset.name, args.cl_default), device).to(device)
        if args.model.proj_layers is not None:
            backbone.projector.set_layers(args.model.proj_layers)

    names = {}
    for model in get_all_models():
        mod = importlib.import_module('models.' + model)
        class_name = {x.lower():x for x in mod.__dir__()}[model.replace('_', '')]
        names[model] = getattr(mod, class_name)
    
    return names[args.model.cl_model](backbone, loss, args, len_train_loader, transform)

