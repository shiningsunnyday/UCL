from tqdm import tqdm
import torch.nn.functional as F 
import torch
import torch.nn as nn
import numpy as np
import copy
import time
from utils.metrics import mask_classes
from collections import OrderedDict
from sklearn.linear_model import LogisticRegression, SGDClassifier

# code copied from https://colab.research.google.com/github/facebookresearch/moco/blob/colab-notebook/colab/moco_cifar10_demo.ipynb#scrollTo=RI1Y8bSImD7N
# test using a knn monitor
def knn_monitor(net, dataset, memory_data_loader, test_data_loader, device, cl_default, task_id, k=200, t=0.1, hide_progress=False, debug=True):
    t_1 = time.time()
    net.eval()
    # classes = len(memory_data_loader.dataset.classes)
    classes = 62
    # classes = 100
    total_top1 = total_top1_mask = total_top5 = total_num = 0.0
    feature_bank = []
    print("knn cuda allocated", torch.cuda.memory_allocated())
    with torch.no_grad():
        # generate feature bank        
        for data, target, *meta_args in tqdm(memory_data_loader, desc='Feature extracting', leave=False, disable=False):
            if cl_default:
                feature = net(data.cuda(non_blocking=True), return_features=True)
            else:
                feature = net(data.cuda(non_blocking=True))
            feature_norm = torch.empty_like(feature)
            F.normalize(feature, dim=1, out=feature_norm)
            feature_norm = feature_norm.detach().cpu()
            feature_bank.append(feature_norm)
            if debug and len(feature_bank)*feature.shape[0] > 200: break
        t_2 = time.time()
        # print("feature bank generation took", t_2-t_1, "seconds")
        # [D, N]        
        feature_bank = torch.cat(feature_bank, dim=0).t().contiguous()
        # [N]
        # feature_labels = torch.tensor(memory_data_loader.dataset.targets - np.amin(memory_data_loader.dataset.targets), device=feature_bank.device)
        feature_labels = torch.tensor(memory_data_loader.dataset.targets, device=feature_bank.device)
        # loop test data to predict the label by weighted knn search
        test_bar = tqdm(test_data_loader, desc='kNN', disable=False)
        for data, target, *meta_args in test_bar:
            data = data.cuda(non_blocking=True)
            if cl_default:
                feature = net(data, return_features=True)
            else:
                feature = net(data)
            feature = F.normalize(feature, dim=1)
            feature = feature.detach().cpu()
            pred_scores = knn_predict(feature, feature_bank, feature_labels, classes, k, t)
            pred_scores

            total_num += data.shape[0]
            _, preds = torch.max(pred_scores.data, 1)
            total_top1 += torch.sum(preds == target).item()
            
            pred_scores = mask_classes(pred_scores, dataset, task_id)
            _, preds = torch.max(pred_scores.data, 1)
            total_top1_mask += torch.sum(preds == target).item()
            if debug: break
        t_3 = time.time()
        # print("knn test took", t_3-t_1, "seconds")
    return total_top1 / total_num * 100, total_top1_mask / total_num * 100

def get_acc(preds, labels):
    return np.mean(preds == labels)

def normalize_features(features, normalize_index):
    # normalize_index is the index to compute mean and std-dev
    # TODO: consider changing to axis=0
    mean = np.mean(features[normalize_index])
    stddev = np.std(features[normalize_index])
    normalized_features = []
    for i in range(len(features)):
        normalized_features.append((features[i] - mean) / stddev)
    return normalized_features

def test_log_reg_warm_starting(features, labels, train_index, test_indices, val_index, loader_names,
                               num_cs=100, start_c=-7, end_c=2, max_iter=200, random_state=0):
    L = len(features)
    # TODO: figure out what this should be based on initial results.
    Cs = np.logspace(start_c, end_c, num_cs)
    clf = LogisticRegression(random_state=random_state, warm_start=True, max_iter=max_iter)
    #.fit(features[m][train_index], labels[m][train_index])
    accs = []
    best_acc = -1.0
    best_clf, best_coef, best_intercept, best_i, best_c = None, None, None, None, None
    for i, C in zip(range(len(Cs)), Cs):
        clf.C = C
        clf.fit(features[train_index], labels[train_index])
        cur_accs = []
        for l in test_indices:
            cur_preds = clf.predict(features[l])
            # These names are selected to be consistent with fine-tuning results.
            # If you update these, please update scripts/run_adaptation_experiments.py
            if l == train_index:
                key = 'train/acc'
            else:
                key = 'test_acc/' + loader_names[l]
            cur_acc = get_acc(cur_preds, labels[l])
            # Don't multiply by 100, we multiply later in summarize_linprobe_results.py
            cur_accs.append((key, cur_acc))
            if l == val_index and cur_acc > best_acc:
                best_acc = cur_acc
                best_clf = copy.deepcopy(clf)
                best_coef = copy.deepcopy(clf.coef_)
                best_intercept = copy.deepcopy(clf.intercept_)
                best_i = i
                best_c = C
        print(cur_accs, flush=True)
        result_row = OrderedDict([('C', C)] + cur_accs)
        accs.append(result_row)
    return best_clf, best_coef, best_intercept, best_c, best_i, accs

def logistic_monitor(net, dataset, memory_data_loader, test_data_loader, device, cl_default, task_id, k=200, t=0.1, hide_progress=False, debug=False):
    features_and_labels = []
    features = []
    targets = []
    target_set = set()
    for (data, target, *meta_args) in tqdm(memory_data_loader, desc='Feature extracting', leave=False, disable=False):        
        if cl_default:
            feature = net(data.cuda(non_blocking=True), return_features=True)
        else:
            feature = net(data.cuda(non_blocking=True))
        feature = feature.detach()
        features.append(feature)
        targets.append(target)
        target_set |= set(target.numpy().tolist())
        if debug and len(target_set) > 1:
            break

    feature = torch.cat(features, dim=0)
    label = torch.cat(targets, dim=0)
        
    features_and_labels.append((feature, label))

    features = []
    targets = []
    for (data, target, *meta_args) in tqdm(test_data_loader, desc='Feature extracting', leave=False, disable=False):        
        if cl_default:
            feature = net(data.cuda(non_blocking=True), return_features=True)
        else:
            feature = net(data.cuda(non_blocking=True))
        feature = feature.detach()
        features.append(feature)
        targets.append(target)
        if debug: break

    feature = torch.cat(features, dim=0)
    label = torch.cat(targets, dim=0)

    features_and_labels.append((feature, label))

    features = [x[0].cpu().numpy() for x in features_and_labels]
    labels = [x[1].cpu().numpy() for x in features_and_labels]
    normalized_features = normalize_features(features, 0)
    clf, coef, intercept, best_c, best_i, accs = test_log_reg_warm_starting(
            normalized_features, labels, 0, [0, 1], val_index=1,
            loader_names=["train", "test"], num_cs=10, random_state=0)
        
    
    return accs[best_i]['train/acc'], accs[best_i]['test_acc/test'], accs[best_i]['C']


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
            target = target % dataset.N_CLASSES_PER_TASK
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
            target = target % dataset.N_CLASSES_PER_TASK
            total_top_1 += (probe(feature).argmax(1) == target).sum().item()
            total_num += data.shape[0]

    test_acc = total_top_1 / total_num * 100
    total_top_1 = total_num = 0
    with torch.no_grad():
    
        test_bar = tqdm(memory_data_loader, desc='probe', disable=True)
        for data, target in test_bar:
            data, target = data.cuda(non_blocking=True), target.cuda(non_blocking=True)
            if cl_default:
                feature = net(data, return_features=True)
            else:
                feature = net(data)
            target = target % dataset.N_CLASSES_PER_TASK
            total_top_1 += (probe(feature).argmax(1) == target).sum().item()
            total_num += data.shape[0]

    train_acc = total_top_1 / total_num * 100
        
    return train_acc, test_acc


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
