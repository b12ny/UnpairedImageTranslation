import os
import random
import glob

from chainer.dataset import dataset_mixin
import numpy as np
from PIL import Image

from chainercv.transforms import random_crop,center_crop,random_flip
from chainercv.transforms import resize
from chainercv.utils import read_image

from consts import dtypes

## load images everytime from disk: slower but low memory usage
class DatasetOutMem(dataset_mixin.DatasetMixin):
    def __init__(self, path, args, base, rang, random=0):
        self.path = path
        self.names = []
        self.random = random
        self.color=not args.grey
        self.ch = 3 if self.color else 1
        self.imgtype=args.imgtype
        self.dtype = dtypes[args.dtype]
        self.base = base # used only with npy files
        self.range = rang
        for fn in glob.glob(os.path.join(self.path,"**/*.{}".format(self.imgtype)), recursive=True):
            self.names.append(fn)
        if args.crop_height and args.crop_width:
            self.crop = (args.crop_height,args.crop_width)
        else:
            self.crop=None
        self.names = sorted(self.names)
        print("Cropped to: ",self.crop)
        print("Loaded: {} images from {}".format(len(self.names),path))

    def __len__(self):
        return len(self.names)

    def get_img_path(self, i):
        return(self.names[i])

    def var2img(self,var):  # [-1,1] => [0,255]
        return((1.0+var)*127.5)

    def img2var(self,img):  # [0,255] => [-1,1]
        return(img/127.5 - 1.0)

    def get_example(self, i):
        if self.imgtype == "npy":
            img = np.load(self.get_img_path(i))
            img = 2*(np.clip(img,self.base,self.base+self.range)-self.base)/self.range-1.0
            if len(img.shape) == 2:
                img = img[np.newaxis,]
        else:
            img = self.img2var(read_image(self.get_img_path(i),color=self.color))
        
#        img = resize(img, (self.resize_to, self.resize_to))
        if self.crop:
            H, W = self.crop
        else:
            H, W = ( 16*((img.shape[1]-2*self.random)//16), 16*((img.shape[2]-2*self.random)//16) )
        if img.shape[1]<H+2*self.random or img.shape[2] < W+2*self.random:
            p = max(H+2*self.random-img.shape[1],W+2*self.random-img.shape[2])
            img = np.pad(img,((0,0),(p,p),(p,p)),'edge')
        img = random_crop(center_crop(img, (H+2*self.random,W+2*self.random)),(H,W))
        if self.random:
            img = random_flip(img, x_random=True)
        return img.astype(self.dtype)

    def mask(self,fn):
        img = Image.open(fn)
        # mask
        if img.mode == 'RGBA':
            mask = (np.array(img.split()[-1]) > 0)
        else:
            mask = np.ones( (img.shape[1],img.shape[2]), dtype=bool)
        # convert to [C,H,W]
        img = np.asarray(img, dtype=np.float32)
        if img.ndim == 2:
            img = img[np.newaxis]
        else:
            img = img.transpose((2, 0, 1))[:3,:,:]
        img = img * mask
        return img2var(img)
