from tqdm import tqdm
import torch.nn.functional as F 
import torch
import torch.nn as nn
import numpy as np
import time
from utils.metrics import mask_classes

# code copied from https://colab.research.google.com/github/facebookresearch/moco/blob/colab-notebook/colab/moco_cifar10_demo.ipynb#scrollTo=RI1Y8bSImD7N
# test using a knn monitor
def knn_monitor(net, dataset, memory_data_loader, test_data_loader, device, cl_default, task_id, k=200, t=0.1, hide_progress=False):
    t_1 = time.time()
    net.eval()
    # classes = len(memory_data_loader.dataset.classes)
    classes = 100
    total_top1 = total_top1_mask = total_top5 = total_num = 0.0
    feature_bank = []
    with torch.no_grad():
        # generate feature bank        
        for data, target in tqdm(memory_data_loader, desc='Feature extracting', leave=False, disable=True):
            if cl_default:
                feature = net(data.cuda(non_blocking=True), return_features=True)
            else:
                feature = net(data.cuda(non_blocking=True))
            feature_norm = torch.empty_like(feature)
            F.normalize(feature, dim=1, out=feature_norm)
            feature_bank.append(feature_norm)
        t_2 = time.time()
        # print("feature bank generation took", t_2-t_1, "seconds")
        # [D, N]        
        feature_bank = torch.cat(feature_bank, dim=0).t().contiguous()
        # [N]
        # feature_labels = torch.tensor(memory_data_loader.dataset.targets - np.amin(memory_data_loader.dataset.targets), device=feature_bank.device)
        feature_labels = torch.tensor(memory_data_loader.dataset.targets, device=feature_bank.device)
        # loop test data to predict the label by weighted knn search
        test_bar = tqdm(test_data_loader, desc='kNN', disable=True)
        for data, target in test_bar:
            data, target = data.cuda(non_blocking=True), target.cuda(non_blocking=True)
            if cl_default:
                feature = net(data, return_features=True)
            else:
                feature = net(data)
            feature = F.normalize(feature, dim=1)
            
            pred_scores = knn_predict(feature, feature_bank, feature_labels, classes, k, t)

            total_num += data.shape[0]
            _, preds = torch.max(pred_scores.data, 1)
            total_top1 += torch.sum(preds == target).item()
            
            pred_scores = mask_classes(pred_scores, dataset, task_id)
            _, preds = torch.max(pred_scores.data, 1)
            total_top1_mask += torch.sum(preds == target).item()
        t_3 = time.time()
        # print("knn test took", t_3-t_1, "seconds")

    return total_top1 / total_num * 100, total_top1_mask / total_num * 100

def probe_monitor(net, dataset, memory_data_loader, test_data_loader, device, cl_default, task_id, k=200, t=0.1, hide_progress=False):
    probe = nn.Linear(512, dataset.N_CLASSES_PER_TASK).cuda()
    optim = torch.optim.SGD(probe.parameters(), lr=0.1*memory_data_loader.batch_size/256, momentum=0.9, nesterov=True)
    loss_function = nn.CrossEntropyLoss()
    min_loss = float("inf")    
    loss = float("-inf")
    i = 0
    avg_loss = 0
    while True:
        avg_loss = 0
        for data, target in tqdm(memory_data_loader, desc='Feature extracting', leave=False, disable=True):
            optim.zero_grad()
            if cl_default:
                feature = net(data.cuda(non_blocking=True), return_features=True)
            else:
                feature = net(data.cuda(non_blocking=True))
            feature = feature.detach()
            feature_norm = torch.empty_like(feature)
            F.normalize(feature, dim=1, out=feature_norm)
            loss = loss_function(probe(feature_norm), target.cuda())
            avg_loss += loss * data.shape[0]
            loss.backward()
            optim.step()

        avg_loss = (avg_loss/(memory_data_loader.__len__())).item()  
        print(f"pass {i} probe loss: {avg_loss}")

        if np.abs(min_loss - avg_loss) < 1e-2:
            break

        min_loss = min(min_loss, avg_loss)        
        i += 1

    total_top_1 = total_num = 0
    with torch.no_grad():
    
        test_bar = tqdm(test_data_loader, desc='probe', disable=True)
        for data, target in test_bar:
            data, target = data.cuda(non_blocking=True), target.cuda(non_blocking=True)
            if cl_default:
                feature = net(data, return_features=True)
            else:
                feature = net(data)
            total_top_1 += (probe(feature).argmax(1) == target).sum().item()
            total_num += data.shape[0]
        
    return total_top_1 / total_num * 100, total_top_1 / total_num * 100


# knn monitor as in InstDisc https://arxiv.org/abs/1805.01978
# implementation follows http://github.com/zhirongw/lemniscate.pytorch and https://github.com/leftthomas/SimCLR
def knn_predict(feature, feature_bank, feature_labels, classes, knn_k, knn_t):
    # compute cos similarity between each feature vector and feature bank ---> [B, N]
    sim_matrix = torch.mm(feature, feature_bank)
    # [B, K]
    sim_weight, sim_indices = sim_matrix.topk(k=knn_k, dim=-1)
    # [B, K]
    sim_labels = torch.gather(feature_labels.expand(feature.size(0), -1), dim=-1, index=sim_indices)
    sim_weight = (sim_weight / knn_t).exp()

    # counts for each class
    one_hot_label = torch.zeros(feature.size(0) * knn_k, classes, device=sim_labels.device)
    # [B*K, C]
    one_hot_label = one_hot_label.scatter(dim=-1, index=sim_labels.view(-1, 1), value=1.0)
    # weighted score ---> [B, C]
    pred_scores = torch.sum(one_hot_label.view(feature.size(0), -1, classes) * sim_weight.unsqueeze(dim=-1), dim=1)

    return pred_scores
