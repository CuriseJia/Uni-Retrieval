import argparse
import os
from tqdm import tqdm
import torch
import numpy as np
import torch.nn.functional as F
from torch.utils.data import DataLoader

from src.prompt.model import ShallowPromptTransformer
from src.prompt.data import Image2ImageDataset, Image2TextDataset
from src.prompt.utils import init_distributed_mode, setup_seed, get_rank, get_world_size, is_main_process, save_loss

def parse_args():
    parser = argparse.ArgumentParser(description='Parse args for SixModal Prompt Tuning.')

    # project settings
    parser.add_argument('--output_dir', default='output/')
    parser.add_argument('--resume', default='output/best.pth', type=str, help='load checkpoints from given path')
    parser.add_argument('--device', default='cuda:1')
    parser.add_argument('--seed', default=42, type=int)
    parser.add_argument('--world_size', default=1, type=int, help='number of distributed processes')
    parser.add_argument('--dist_url', default='env://', help='url used to set up distributed training')
    parser.add_argument('--distributed', default=False, type=bool)
    parser.add_argument('--num_workers', default=4, type=int)
    parser.add_argument("--local_rank", type=int)

    # data settings
    parser.add_argument("--type", type=str, default='image2text', help='choose train image2text or image2image.')
    parser.add_argument("--train_dataset_path", type=str, default='/public/home/jiayanhao/imagenet/train/')
    parser.add_argument("--test_dataset_path", type=str, default='/public/home/jiayanhao/imagenet/val/')
    parser.add_argument("--train_json_path", type=str, default='/public/home/jiayanhao/imagenet/200-original-long.json')
    parser.add_argument("--test_json_path", type=str, default='/public/home/jiayanhao/imagenet/200-original-long-val.json')
    parser.add_argument("--batch_size", type=int, default=256)
    parser.add_argument("--epochs", type=int, default=100)

    # model settings
    parser.add_argument('--prompt', type=str, default='ShallowPrompt', help='ShallowPrompt or DeepPrompt')
    parser.add_argument('--n_prompts', type=int, default=3)
    parser.add_argument('--prompt_dim', type=int, default=50176)

    # optimizer settings
    parser.add_argument('--clip_ln_lr', type=float, default=1e-4)
    parser.add_argument('--prompt_lr', type=float, default=1e-4)

    args = parser.parse_args()
    return args



def train(args, model, device, dataloader, optimizer):
    model.train()

    best_loss = 10000000

    losses = []
    epoches = []
    count = 0

    if args.type == 'image2text':
        for epoch in tqdm(range(args.epochs)):
            temp_loss = []

            for data in enumerate(tqdm(dataloader)):
                image = data[1][0].to(device, non_blocking=True)
                long_caption = model.tokenizer(data[1][1]).to(device, non_blocking=True)
                negative_image = data[1][2].to(device, non_blocking=True)

                image_feature = model(image, dtype='image')
                negative_feature = model(negative_image, dtype='image')
                text_feature = model(long_caption, dtype='text')

                loss = model.triplet_loss(image_feature, text_feature, negative_feature)

                temp_loss.append(loss.detach().cpu().numpy())

                print("loss: {:.6f}".format(loss))

                optimizer.zero_grad()
                loss.backward()
                optimizer.step()
            
            res = round(sum(temp_loss)/len(temp_loss), 6)
            print("epoch_{} loss is {}.".format(epoch, res))
            losses.append(res)
            epoches.append(epoch)

            if res<best_loss:
                best_loss = res
                save_obj = model.state_dict()
                torch.save(save_obj, os.path.join(args.output_dir, 'epoch_mar1_{}.pth'.format(epoch)))
                count = 0
            else:
                count +=1
            
            if best_loss < 0.0001 or count >= 5:
                break
    
    else:   # image2image retrival
        for epoch in tqdm(range(args.epochs)):
            temp_loss = []

            for data in enumerate(tqdm(dataloader)):
                original_image = data[1][0].to(device, non_blocking=True)
                retrival_image = data[1][1].to(device, non_blocking=True)
                negative_image = data[1][2].to(device, non_blocking=True)

                original_feature = model(original_image, dtype='image')
                retrival_feature = model(retrival_image, dtype='image')
                negative_feature = model(negative_image, dtype='image')

                loss = model.triplet_loss(original_feature, retrival_feature, negative_feature)

                temp_loss.append(loss.detach().cpu().numpy())

                print("loss: {:.6f}".format(loss))

                optimizer.zero_grad()
                loss.backward()
                optimizer.step()
            
            res = round(sum(temp_loss)/len(temp_loss), 6)
            print("epoch_{} loss is {}.".format(epoch, res))
            losses.append(res)
            epoches.append(epoch)

            if res<best_loss:
                best_loss = res
                save_obj = model.state_dict()
                torch.save(save_obj, os.path.join(args.output_dir, 'epoch_mar1_{}.pth'.format(epoch)))
                count = 0
            else:
                count +=1
            
            if best_loss < 0.0001 or count >= 5:
                break
    
    return losses, epoches


def eval(args, model, dataloader):
    model.eval()

    for data in tqdm(enumerate(dataloader)):
        image = data[1][0].to(device, non_blocking=True)
        long_caption = model.tokenizer(data[1][1]).to(device, non_blocking=True)

        image_feature = model(image, dtype='image')
        text_feature = model(long_caption, dtype='text')

        image_feature = F.normalize(image_feature, dim=-1)
        text_feature = F.normalize(text_feature, dim=-1)

        prob = torch.softmax((100.0 * image_feature @ text_feature.T), dim=-1)

        print(prob)

     # def testing_step(self):
    #     test_batch = self.get_fake_batch()
    #     img_tensor, txt_tensor = batch[:2]
    #     image_features = self.forward(img_tensor, dtype='image')
    #     text_features = self.forward(txt_tensor, dtype='text')
    #     image_features /= image_features.norm(dim=-1, keepdim=True)
    #     text_features /= text_features.norm(dim=-1, keepdim=True)
    #     text_probs = (100.0 * image_features @ text_features.T).softmax(dim=-1)
    #     print(text_probs)


if __name__ == "__main__":
    args = parse_args()
    # init_distributed_mode(args)
    setup_seed(args.seed)
    device = torch.device(args.device)

    model = ShallowPromptTransformer(args)
    model = model.to(device)
    model = model.load_state_dict(torch.load(args.resume))

    # train_dataset = Image2TextDataset(args.train_dataset_path, args.train_json_path, model.pre_process_train, 'train')
    test_dataset = Image2TextDataset(args.test_dataset_path, args.test_json_path, model.pre_process_val, 'test')

    optimizer = torch.optim.Adam([
            {'params': model.openclip.parameters(), 'lr': args.clip_ln_lr},
            {'params': [model.img_prompt], 'lr': args.prompt_lr}])


    # train_loader = DataLoader(dataset=train_dataset, 
    #                         batch_size=args.batch_size,
    #                         num_workers=args.num_workers,
    #                         pin_memory=True,
    #                         prefetch_factor=16,
    #                         shuffle=False,
    #                         drop_last=True
    #                         )
    test_loader = DataLoader(dataset=test_dataset, 
                            batch_size=args.batch_size,
                            num_workers=args.num_workers,
                            pin_memory=True,
                            prefetch_factor=16,
                            shuffle=False,
                            drop_last=True
                            )


    # loss, epochs = train(args, model, device, train_loader, optimizer)

    # save_loss(loss, epochs)
    
    eval(args, model, test_loader)