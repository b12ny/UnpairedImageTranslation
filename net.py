import functools

import chainer
import chainer.functions as F
import chainer.links as L
import numpy as np

from instance_normalization import InstanceNormalization
#from instancenorm import InstanceNormalization


def get_norm_layer(norm='instance'):
    # unchecked: init weight of bn
    ## NOTE: gamma and beta affects a lot
    if norm == 'batch':
        norm_layer = functools.partial(L.BatchNormalization, use_gamma=False, use_beta=False)
    elif norm == 'batch_aff':
        norm_layer = functools.partial(L.BatchNormalization, use_gamma=True, use_beta=True)
    elif norm == 'rbatch':
        norm_layer = functools.partial(L.BatchRenormalization, use_gamma=False, use_beta=False)
    elif norm == 'instance':
        norm_layer = functools.partial(InstanceNormalization, use_gamma=False, use_beta=False)
    elif norm == 'instance_aff':
        norm_layer = functools.partial(InstanceNormalization, use_gamma=True, use_beta=True)
    elif norm == 'fnorm':
        norm_layer = fnorm_wrapper
    else:
        raise NotImplementedError(
            'normalization layer [%s] is not found' % norm)
    return norm_layer

def fnorm_wrapper(ch):
    return feature_vector_normalization

def feature_vector_normalization(x, eps=1e-8):
    # x: (B, C, H, W)
    alpha = 1.0 / F.sqrt(F.mean(x*x, axis=1, keepdims=True) + eps)
    return F.broadcast_to(alpha, x.data.shape) * x

class EqualizedConv2d(chainer.Chain):
    def __init__(self, in_ch, out_ch, ksize, stride, pad, pad_type='zero', equalised=False, nobias=False,separable=False):
        self.equalised = equalised
        self.separable = separable
        self.pad_type = pad_type
        self.pad = pad
        if equalised:
            w = chainer.initializers.Normal(1.0) # equalized learning rate
        else:
            w = chainer.initializers.HeNormal()
        bias = chainer.initializers.Zero()
        self.ksize = ksize
        super(EqualizedConv2d, self).__init__()
        with self.init_scope():
            if self.separable:
                self.depthwise = L.Convolution2D(in_ch, in_ch, ksize, stride, initialW=w, nobias=True, groups=in_ch)
                self.pointwise = L.Convolution2D(in_ch, out_ch, 1, 1, initialW=w, nobias=nobias, initial_bias=bias)
            else:
                self.c = L.Convolution2D(in_ch, out_ch, ksize, stride, initialW=w, nobias=nobias, initial_bias=bias)
    def __call__(self, x):
        if self.pad_type=='reflect':
            h = F.pad(x,[[0,0],[0,0],[self.pad,self.pad],[self.pad,self.pad]],mode='reflect')
        else:
            h = F.pad(x,[[0,0],[0,0],[self.pad,self.pad],[self.pad,self.pad]],mode='constant',constant_values=0) 
        if self.equalised:
            b,c,_,_ = h.shape
            inv_c = np.sqrt(2.0/(c*self.ksize**2))
            h = inv_c * h
        if self.separable:
            return self.pointwise(self.depthwise(h))
        return self.c(h)

class EqualizedDeconv2d(chainer.Chain):
    def __init__(self, in_ch, out_ch, ksize, stride, pad, equalised=False, nobias=False,separable=False):
        self.equalised = equalised
        self.separable = separable
        self.pad = pad
        self.ksize = ksize
        if equalised:
            w = chainer.initializers.Normal(1.0) # equalized learning rate
        else:
            w = chainer.initializers.HeNormal()
        bias = chainer.initializers.Zero()
        self.ksize = ksize
        super(EqualizedDeconv2d, self).__init__()
        with self.init_scope():
            if self.separable:
                self.depthwise = L.Deconvolution2D(in_ch, in_ch, ksize, stride, pad, initialW=w, nobias=True, groups=in_ch)
                self.pointwise = L.Deconvolution2D(in_ch, out_ch, 1, 1, initialW=w, nobias=nobias, initial_bias=bias)
            else:
                self.c = L.Deconvolution2D(in_ch, out_ch, ksize, stride, pad, initialW=w, nobias=nobias,initial_bias=bias)
    def __call__(self, x):
        h=x
        if self.equalised:
            b,c,_,_ = h.shape
            inv_c = np.sqrt(2.0/(c*self.ksize**2))
            h = inv_c * h
        if self.separable:
            h = self.pointwise(self.depthwise(h))
        else:
            h = self.c(h)
        if(self.ksize==3):
            h = F.pad(h,[[0,0],[0,0],[0,1],[0,1]],mode='reflect')
        return h

class ResBlock(chainer.Chain):
    def __init__(self, ch, norm='instance', activation=F.relu, equalised=False, separable=False):
        super(ResBlock, self).__init__()
        self.activation = activation
        self.use_norm = False if norm=='none' else True
        nobias = True if 'aff' in norm else False
        with self.init_scope():
            self.c0 = EqualizedConv2d(ch, ch, 3, 1, 1, pad_type='zero', equalised=equalised, nobias=nobias, separable=separable)
            self.c1 = EqualizedConv2d(ch, ch, 3, 1, 1, pad_type='zero', equalised=equalised, nobias=nobias, separable=separable)
            if self.use_norm:
                self.norm0 = get_norm_layer(norm)(ch)
                self.norm1 = get_norm_layer(norm)(ch)

    def __call__(self, x):
        h = self.c0(x)
        if self.use_norm:
            h = self.norm0(h)
        h = self.activation(h)
        h = self.c1(h)
        if self.use_norm:
            h = self.norm1(h)
        return self.activation(h + x)


class CBR(chainer.Chain):
    def __init__(self, ch0, ch1, ksize=3, pad=1, norm='instance',
                 sample='down', activation=F.relu, dropout=False, equalised=False):
        super(CBR, self).__init__()
        self.activation = activation
        self.dropout = dropout
        self.sample = sample
        self.use_norm = False if norm=='none' else True
        nobias = True if 'aff' in norm else False

        with self.init_scope():
            if sample == 'down':
                self.c = EqualizedConv2d(ch0, ch1, ksize, 2, pad, equalised=equalised, nobias=nobias)
            elif sample == 'none-7':
                self.c = EqualizedConv2d(ch0, ch1, 7, 1, 3, pad_type='reflect', equalised=equalised, nobias=nobias) 
            elif sample == 'deconv':
                self.c = EqualizedDeconv2d(ch0, ch1, ksize, 2, pad, equalised=equalised, nobias=nobias)
            elif sample in ['unpool_res','maxpool_res','avgpool_res']:
                self.c = EqualizedConv2d(ch0, ch1, ksize, 1, pad, equalised=equalised, nobias=nobias)
                if self.use_norm:
                    self.norm0 = get_norm_layer(norm)(ch1)
                self.cr = EqualizedConv2d(ch1, ch1, ksize, 1, pad, equalised=equalised, nobias=nobias)
            else:
                self.c = EqualizedConv2d(ch0, ch1, ksize, 1, pad, equalised=equalised, nobias=nobias)
            if self.use_norm:
                self.norm = get_norm_layer(norm)(ch1)

    def __call__(self, x):
        if self.sample in ['down','none','none-7','deconv']:
            h = self.c(x)
        elif self.sample == 'unpool':
            h = F.unpooling_2d(x, 2, 2, 0, cover_all=False)
            h = self.c(h)
        elif self.sample == 'unpool_res':
            h = F.unpooling_2d(x, 2, 2, 0, cover_all=False)
            h0 = self.c(h)
            if self.use_norm:
                h = self.norm0(h0)
            if self.activation is not None:
                h = self.activation(h)
            h = h0 + self.cr(h)
        elif self.sample == 'maxpool':
            h = self.c(x)
            h = F.max_pooling_2d(h, 2, 2, 0)
        elif self.sample == 'maxpool_res':
            h = self.c(x)
            if self.use_norm:
                h = self.norm0(h)
            if self.activation is not None:
                h = self.activation(h)
            h = x+self.cr(h)
            h = F.max_pooling_2d(h, 2, 2, 0)
        elif self.sample == 'avgpool':
            h = self.c(x)
            h = F.average_pooling_2d(h, 2, 2, 0)
        elif self.sample == 'avgpool_res':
            h = self.c(x)
            if self.use_norm:
                h = self.norm0(h)
            if self.activation is not None:
                h = self.activation(h)
            h = x+self.cr(h)
            h = F.average_pooling_2d(h, 2, 2, 0)
        else:
            print('unknown sample method %s' % self.sample)
            exit()
        if self.use_norm:
            h = self.norm(h)
        if self.dropout:
            h = F.dropout(h, ratio=self.dropout)
        if self.activation is not None:
            h = self.activation(h)
        return h

class LBR(chainer.Chain):
    def __init__(self, height, width, ch, norm='none',
                 activation=F.relu, dropout=False):
        super(LBR, self).__init__()
        self.activation = activation
        self.dropout = dropout
        self.use_norm = False if norm=='none' else True
        self.ch = ch
        self.width = width
        self.height = height
        with self.init_scope():
            self.l0 = L.Linear(ch*height*width)
            self.l1 = L.Linear(ch*height*width)
            if self.use_norm:
                self.norm = get_norm_layer(norm)(ch*height*width)

    def __call__(self, x):
        h = self.l0(x)
        if self.use_norm:
            h = self.norm(h)
        if self.dropout:
            h = F.dropout(h, ratio=self.dropout)
        if self.activation is not None:
            h = self.activation(h)
        h = self.l1(h)
        if self.use_norm:
            h = self.norm(h)
        if self.dropout:
            h = F.dropout(h, ratio=self.dropout)
        if self.activation is not None:
            h = self.activation(h)
        return F.reshape(h,(-1,self.ch,self.height,self.width))

class Encoder(chainer.Chain):    ## we have to know the the number of input channels to decode!
    def __init__(self, args):
        super(Encoder, self).__init__()
        self.n_resblock = args.gen_nblock
        self.chs = args.gen_chs
        self.unet = args.unet
        with self.init_scope():
            # nn.ReflectionPad2d in original
            self.c0 = CBR(args.ch, self.chs[0], norm=args.gen_norm, sample=args.gen_sample, activation=args.gen_activation, equalised=args.eqconv)
            for i in range(1,len(self.chs)):
                setattr(self, 'd' + str(i), CBR(self.chs[i-1], self.chs[i], ksize=args.gen_ksize, norm=args.gen_norm, sample=args.gen_down, activation=args.gen_activation, dropout=args.gen_dropout, equalised=args.eqconv))
            for i in range(self.n_resblock):
                setattr(self, 'r' + str(i), ResBlock(self.chs[-1], norm=args.gen_norm, activation=args.gen_activation, equalised=args.eqconv))
    def __call__(self, x):
        h = [self.c0(x)]  
        for i in range(1,len(self.chs)):
            h.append(getattr(self, 'd' + str(i))(h[-1]))
#            print(h[-1].data.shape)
        e = h[-1]
        for i in range(self.n_resblock):
            e = getattr(self, 'r' + str(i))(e)
        h.append(e)
        return h

class Decoder(chainer.Chain):
    def __init__(self, args):
        super(Decoder, self).__init__()
        self.n_resblock = args.gen_nblock
        self.chs = args.gen_chs
        self.unet = args.unet
        with self.init_scope():
            for i in range(self.n_resblock):
                setattr(self, 'r' + str(i), ResBlock(self.chs[-1], norm=args.gen_norm, activation=args.gen_activation, equalised=args.eqconv))
            if self.unet in ['no_last','with_last']:
                for i in range(1,len(self.chs)):
                    setattr(self, 'ua' + str(i), CBR(2*self.chs[-i], self.chs[-i-1], ksize=args.gen_ksize, norm=args.gen_norm, sample=args.gen_up, activation=args.gen_activation, dropout=args.gen_dropout, equalised=args.eqconv))
                if self.unet=='no_last':
                    setattr(self, 'cl',CBR(self.chs[0], args.ch, norm='none', sample=args.gen_sample, activation=args.gen_out_activation, equalised=args.eqconv))
                else:
                    setattr(self, 'cl',CBR(2*self.chs[0], args.ch, norm='none', sample=args.gen_sample, activation=args.gen_out_activation, equalised=args.eqconv))
            else:
                for i in range(1,len(self.chs)):
                    setattr(self, 'ua' + str(i), CBR(self.chs[-i], self.chs[-i-1], norm=args.gen_norm, sample=args.gen_up, activation=args.gen_activation, dropout=args.gen_dropout, equalised=args.eqconv))
                setattr(self, 'cl',CBR(self.chs[0], args.ch, norm='none', sample=args.gen_sample, activation=args.gen_out_activation, equalised=args.eqconv))

    def __call__(self, h):
        e = h[-1]
        for i in range(self.n_resblock):
            e = getattr(self, 'r' + str(i))(e)
        for i in range(1,len(self.chs)):
            if self.unet in ['no_last','with_last']:
                e = getattr(self, 'ua' + str(i))(F.concat([e,h[-i-1]]))
            else:
                e = getattr(self, 'ua' + str(i))(e)
        if self.unet=='no_last':
            e = getattr(self, 'cl')(e)
        elif self.unet=='with_last':
            e = getattr(self, 'cl')(F.concat([e,h[0]]))
        else:
            e = getattr(self, 'cl')(e)
        return e

class Generator(chainer.Chain):
    def __init__(self, args):
        super(Generator, self).__init__()
        self.n_resblock = args.gen_nblock
        self.chs = args.gen_chs
        self.unet = args.unet
        self.gen_fc = args.gen_fc
        with self.init_scope():
            if self.gen_fc:
                self.l0 = LBR(args.crop_height,args.crop_width,args.ch)
            self.c0 = CBR(args.ch, self.chs[0], norm=args.gen_norm, sample=args.gen_sample, activation=args.gen_activation, equalised=args.eqconv)
            for i in range(1,len(self.chs)):
                setattr(self, 'd' + str(i), CBR(self.chs[i-1], self.chs[i], ksize=args.gen_ksize, norm=args.gen_norm, sample=args.gen_down, activation=args.gen_activation, dropout=args.gen_dropout, equalised=args.eqconv))
            for i in range(self.n_resblock):
                setattr(self, 'r' + str(i), ResBlock(self.chs[-1], norm=args.gen_norm, activation=args.gen_activation, equalised=args.eqconv))
            if self.unet in ['no_last','with_last']:
                for i in range(1,len(self.chs)):
                    setattr(self, 'ua' + str(i), CBR(2*self.chs[-i], self.chs[-i-1],  norm=args.gen_norm, sample=args.gen_up, activation=args.gen_activation, dropout=args.gen_dropout, equalised=args.eqconv))
                if self.unet=='no_last':
                    setattr(self, 'cl',CBR(self.chs[0], args.ch,norm='none', sample=args.gen_sample, activation=args.gen_out_activation, equalised=args.eqconv))
                else:
                    setattr(self, 'cl',CBR(2*self.chs[0], args.ch, norm='none', sample=args.gen_sample, activation=args.gen_out_activation, equalised=args.eqconv))
            else:
                for i in range(1,len(self.chs)):
                    setattr(self, 'ua' + str(i), CBR(self.chs[-i], self.chs[-i-1], norm=args.gen_norm, sample=args.gen_up, activation=args.gen_activation, dropout=args.gen_dropout, equalised=args.eqconv))
                setattr(self, 'cl',CBR(self.chs[0], args.ch,norm='none', sample=args.gen_sample, activation=args.gen_out_activation, equalised=args.eqconv))

    def __call__(self, x):
        if self.gen_fc:
            h = self.l0(x)
        else:
            h=x
        h = [self.c0(h)]
        for i in range(1,len(self.chs)):
            h.append(getattr(self, 'd' + str(i))(h[-1]))
#            print(h[-1].data.shape)
        e = h[-1]
        for i in range(self.n_resblock):
            e = getattr(self, 'r' + str(i))(e)
        for i in range(1,len(self.chs)):
            if self.unet in ['no_last','with_last']:
                e = getattr(self, 'ua' + str(i))(F.concat([e,h[-1]]))
            else:
                e = getattr(self, 'ua' + str(i))(e)
#            print(e.data.shape)
            del h[-1]
        if self.unet=='no_last':
            e = getattr(self, 'cl')(e)
        elif self.unet=='with_last':
            e = getattr(self, 'cl')(F.concat([e,h[-1]]))
        else:
            e = getattr(self, 'cl')(e)
        del h[-1]
        return e

class Discriminator(chainer.Chain):
    def __init__(self, args):
        super(Discriminator, self).__init__()
        base = args.dis_basech
        self.n_down_layers = args.dis_ndown
        self.activation = args.dis_activation
        self.wgan = args.wgan

        with self.init_scope():
            self.c0 = CBR(None, base, ksize=args.dis_ksize, norm='none', 
                          sample=args.dis_sample, activation=args.dis_activation,
                          dropout=args.dis_dropout, equalised=args.eqconv)

            for i in range(1, self.n_down_layers):
                setattr(self, 'c' + str(i),
                        CBR(base, base * 2, ksize=args.dis_ksize, norm=args.dis_norm,
                            sample=args.dis_down, activation=args.dis_activation, dropout=args.dis_dropout, equalised=args.eqconv))
                base *= 2

            self.csl = CBR(base, base * 2, ksize=args.dis_ksize, norm=args.dis_norm, sample='none', activation=args.dis_activation, dropout=args.dis_dropout, equalised=args.eqconv)
            base *= 2
            self.cl = CBR(base, 1, ksize=args.dis_ksize, norm='none', sample='none', activation=None, dropout=False, equalised=args.eqconv)
            if self.wgan:
                self.fc = L.Linear(None, 1)

    def __call__(self, x):
        h = self.c0(x)
        for i in range(1, self.n_down_layers):
            h = getattr(self, 'c' + str(i))(h)
        h = self.csl(h)
        h = self.cl(h)
        if self.wgan:
            h = self.fc(h)
        return h


class DiscriminatorZ(chainer.Chain):
    def __init__(self, args):
        super(DiscriminatorZ, self).__init__()
        pad = 1
        base = args.dis_basech
        self.n_down_layers = args.z_ndown
        self.activation = args.dis_activation
        self.wgan = args.wgan

        with self.init_scope():
            self.c0 = CBR(args.gen_chs[-1], base, ksize=args.dis_ksize, norm='none', 
                          sample=args.dis_sample, activation=args.dis_activation,
                          dropout=args.dis_dropout, equalised=args.eqconv)

            for i in range(1, self.n_down_layers):
                setattr(self, 'c' + str(i),
                        CBR(base, base * 2, ksize=args.dis_ksize, pad=pad, norm=args.dis_norm,
                            sample=args.dis_down, activation=args.dis_activation, dropout=args.dis_dropout, equalised=args.eqconv))
                base *= 2

            self.csl = CBR(base, base * 2, ksize=args.dis_ksize, pad=pad, norm=args.dis_norm, sample='none', activation=args.dis_activation, dropout=args.dis_dropout, equalised=args.eqconv)
            base *= 2
            self.cl = CBR(base, 1, ksize=args.dis_ksize, pad=pad, norm='none', sample='none', activation=None, dropout=False, equalised=args.eqconv)
            if self.wgan:
                self.fc = L.Linear(None, 1)

    def __call__(self, x):
        h = self.c0(x)
        for i in range(1, self.n_down_layers):
            h = getattr(self, 'c' + str(i))(h)
        h = self.csl(h)
        h = self.cl(h)
        if self.wgan:
            h = self.fc(h)
        return h
        
