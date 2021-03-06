import argparse
import os
import sys

os.environ["CUDA_LAUNCH_BLOCKING"] = "1"

import math
from PIL import Image
import numpy as np
import torch
from torch import nn
from torchvision.datasets import ImageFolder
from torchvision import transforms, models
import torch.nn.functional as F
from torch.autograd import Variable

from tqdm import tqdm

from efficientnet_pytorch.radam import RAdam
from timm.models.efficientnet import *
from efficientnet_pytorch.utils import (
    round_filters,
    round_repeats,
    drop_connect,
    get_same_padding_conv2d,
    get_model_params,
    efficientnet_params,
    load_pretrained_weights,
    Swish,
    MemoryEfficientSwish,
    calculate_output_image_size,
)
from augmentations import *
from dataloader import ProductImageLoader

_IMAGENET_PCA = {
    "eigval": [0.2175, 0.0188, 0.0045],
    "eigvec": [
        [-0.5675, 0.7192, 0.4009],
        [-0.5808, -0.0045, -0.8140],
        [-0.5836, -0.6948, 0.4203],
    ],
}


def DMI_loss(output, target):
    num_classes = 42

    outputs = F.softmax(output, dim=1)
    targets = target.reshape(target.size(0), 1).cpu()
    y_onehot = torch.FloatTensor(target.size(0), num_classes).zero_()
    y_onehot.scatter_(1, targets, 1)
    y_onehot = y_onehot.transpose(0, 1).cuda()
    mat = y_onehot @ outputs
    return -1.0 * torch.log(torch.abs(torch.det(mat.float())) + 0.001)

def train(
    arch,
    model,
    dataloaders,
    dataset_size,
    criterion,
    optimizer,
    scheduler,
    num_epochs,
    valid_loss_min=np.Inf,
):
    device = "cuda:0" if torch.cuda.is_available() else "cpu"
    print(device)
    model = model.to(device)
    # criterion = criterion.to(device)

    for epoch in range(num_epochs):
        print(
            "Epoch {}/{}".format(epoch, num_epochs), "\n-------------------------------"
        )

        for phase in ["val", "train"]:
            if phase == "train":
                model.train()
            else:
                model.eval()
            running_loss = 0
            accuracy = 0
            for features, targets in tqdm(dataloaders[phase]):
                features = features.to(device)
                targets = targets.to(device)

                optimizer.zero_grad()
                outputs = model(features)
                _, preds = torch.max(outputs, 1)
                accuracy += torch.sum(preds == targets.data)
                loss = criterion(outputs, targets)
                running_loss += loss.item() * features.size(0)

                if phase == "train":
                    loss.backward()
                    optimizer.step()
                    scheduler.step()
            print(
                "{} Loss: {:.4f} Acc: {:.4f}".format(
                    phase,
                    running_loss / dataset_size[phase],
                    accuracy.double() / dataset_size[phase],
                )
            )
            if phase == "val" and running_loss <= valid_loss_min:
                print(
                    "Validation loss decreased ({:.6f} --> {:.6f}).  Saving model ...".format(
                        valid_loss_min, running_loss
                    )
                )
                torch.save(model.state_dict(), "model_" + arch + ".pt")
                valid_loss_min = running_loss
    return model, valid_loss_min

if __name__ == "__main__":
    arch = sys.argv[1]
    batch_size = int(sys.argv[2])

    if arch == "B6NS":
        model = tf_efficientnet_b6_ns(True)
        model.classifier = nn.Linear(2304, 42)
    elif arch == "B7NS":
        model = tf_efficientnet_b7_ns(True)
        model.classifier = nn.Linear(2560, 42)
    elif arch == "B1NS":
        model = tf_efficientnet_b1_ns(True)
        model.classifier = nn.Linear(1280, 42)
    elif arch == "B5NS":
        model = tf_efficientnet_b5_ns(True)
        model.classifier = nn.Linear(2048, 42)

    print(model)

    # model.load_state_dict(torch.load('./model_efficient-2-b0.pt'))
    # print(model)
    for param in model.parameters():
        param.requires_grad = True

    train_set = ProductImageLoader(None, "./dataset/train/fold0_train.csv", "train")
    val_set = ProductImageLoader(None, "./dataset/train/fold0_test.csv", "val")

    # TODO: Using the image datasets and the trainforms, define the dataloaders
    dataloaders = {
        "train": torch.utils.data.DataLoader(
            train_set,
            batch_size=batch_size,
            shuffle=True,
            pin_memory=False,
            num_workers=8,
        ),
        "val": torch.utils.data.DataLoader(
            val_set,
            batch_size=batch_size,
            shuffle=False,
            pin_memory=False,
            num_workers=8,
        ),
    }

    dataset_size = {"train": len(train_set), "val": len(val_set)}

    criterion = DMI_loss #nn.CrossEntropyLoss()
    optimizer = RAdam(model.parameters(), lr=1e-4)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingWarmRestarts(optimizer, 2000)
    train(arch, model, dataloaders, dataset_size, criterion, optimizer, scheduler, 100)