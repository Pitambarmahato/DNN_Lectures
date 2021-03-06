import sys

import numpy as np
from scipy.signal import convolve
from tensorflow import keras
from skimage.measure import block_reduce
from skimage.util import view_as_blocks,view_as_windows

from utility_functions import averager, relu, extract_averager_value, sigmoid, relu_prime

# GET DATA

(X_train_full, y_train_full), (X_test, y_test) = keras.datasets.fashion_mnist.load_data()
cond=np.any([y_train_full==1,y_train_full==3],0)

X_train_full=X_train_full[cond]
y_train_full=y_train_full[cond]
X_test=X_test[np.any([y_test==1,y_test==3],0)]
y_test=y_test[np.any([y_test==1,y_test==3],0)]

y_train_full=(y_train_full==3).astype(int)
y_test=(y_test==3).astype(int)

X_train, X_valid = X_train_full[:-1000], X_train_full[-1000:]
y_train, y_valid = y_train_full[:-1000], y_train_full[-1000:]

X_mean = X_train.mean(axis=0, keepdims=True)
X_std = X_train.std(axis=0, keepdims=True) + 1e-7
X_train = (X_train - X_mean) / X_std
X_valid = (X_valid - X_mean) / X_std
X_test = (X_test - X_mean) / X_std

def random_weights(np_seed=None):
    if np_seed:
        np.random.seed(np_seed)
    W1 = np.random.normal(0, 2 / np.sqrt(K * K), size=(K, K))
    W2 = np.random.normal(0, 1 / np.sqrt(max_pooled_image_size * max_pooled_image_size),
                          size=(max_pooled_image_size * max_pooled_image_size))
    return W1,W2

def forward_pass(W1,W2,X,y):
    l0=X
    l0_conv=convolve(l0,W1[::-1,::-1],'same','direct')

    l1=relu(l0_conv)
    l1_max_pooled_raveled = block_reduce(l1, (max_pool_size, max_pool_size), np.max).ravel()
    l2=sigmoid(np.dot(l1_max_pooled_raveled,W2))
    l2=l2.clip(10**-16,1-10**-16)


    loss=-(y*np.log(l2)+(1-y)*np.log(1-l2))
    accuracy=int(y==np.where(l2>0.5,1,0))

    return accuracy,loss

K=3
max_pool_size=1
image_size=X_train.shape[1]
assert image_size % max_pool_size==0, 'Image sizes needs to be multiple of max pool window size'

max_pooled_image_size=int(image_size / max_pool_size)
image_size_embedding_size=image_size+K-1

# PRINT INITIAL LOSS AND ACCURACY (which is computable theoretically)

W1, W2 = random_weights(42)
train_loss=averager()
train_accuracy=averager()
loss_averager_valid=averager()
accuracy_averager_valid=averager()

for X,y in zip(X_train,y_train):
    accuracy,loss=forward_pass(W1,W2,X,y)
    train_loss.send(loss)
    train_accuracy.send(accuracy)

for X,y in zip(X_valid,y_valid):
    accuracy,loss=forward_pass(W1,W2,X,y)
    loss_averager_valid.send(loss)
    accuracy_averager_valid.send(accuracy)

train_loss,train_accuracy,valid_loss,valid_accuracy=map(extract_averager_value,[
                                                        train_loss,
                                                        train_accuracy,
                                                        loss_averager_valid,
                                                        accuracy_averager_valid]
                                                       )

msg='With original weights: train loss {:.2f}, train acc {:.2f}, valid loss {:.2f}, valid acc {:.2f}'.format(
                                                                                                  train_loss,
                                                                                                  train_accuracy,
                                                                                                  valid_loss,
                                                                                                  valid_accuracy
                                                                                                 )
print(msg)

_row_and_col=np.arange(0, image_size, max_pool_size)
_row_and_col=[_row_and_col for _ in range(max_pooled_image_size)]
_rows_adder=np.stack(_row_and_col,axis=1)
_cols_adder=np.stack(_row_and_col,axis=0)
lt0 = np.zeros((image_size + K - 1, image_size + K - 1))

def train_model(W1,W2,num_epochs=5,eta=0.001,update_W1=True,update_W2=True):
    dl1=np.zeros((image_size,image_size))
    for epoch in range(num_epochs):
        train_loss = averager()
        train_accuracy = averager()

        for i in range(len(y_train)):

            # Take a random sample
            k = np.random.randint(len(y_train))
            X = X_train[k]
            y = y_train[k]
            if (i + 1) % 100 == 0:
                sys.stdout.write('{}\r'.format(i+1))

            # First layer is just the input
            l0 = X

            # Embed the image in a bigger image. It would be useful in computing corrections to the
            # convolution filter

            lt0[K // 2:-K // 2 + 1, K // 2:-K // 2 + 1] = l0

            # convolve with the filter
            l0_conv = convolve(l0, W1[::-1, ::-1], 'same')

            # Layer one is Relu applied on the convolution
            l1 = relu(l0_conv)

            # max pooling

            view = view_as_blocks(l1, (max_pool_size, max_pool_size)).reshape(max_pooled_image_size, max_pooled_image_size, -1)
            l1_max_pooled_raveled = np.max(view, axis=2).ravel()
            arg_max_1d = np.argmax(view, axis=2)
            max_rows = (arg_max_1d // 2 + _rows_adder).ravel()
            max_cols = (arg_max_1d % 2 + _cols_adder).ravel()

            # Compute layer 2
            l2 = sigmoid(np.dot(l1_max_pooled_raveled, W2))
            l2 = l2.clip(10 ** -16, 1 - 10 ** -16)

            # Loss and Accuracy
            loss = -(y * np.log(l2) + (1 - y) * np.log(1 - l2))
            accuracy = int(y == np.where(l2 > 0.5, 1, 0))

            # Save the loss and accuracy to a running averager
            train_loss.send(loss)
            train_accuracy.send(accuracy)

            # Derivative of loss wrt the dense layer
            if update_W2:
                dW2 = (((1 - y) * l2 - y * (1 - l2)) * l1_max_pooled_raveled)

            if update_W1:
                # Derivative of loss wrt the output of the first layer
                dl1_max_pooled_raveled = (((1 - y) * l2 - y * (1 - l2)) * W2) #.reshape(half_image_size, half_image_size)
                dl1[max_rows,max_cols]=dl1_max_pooled_raveled

                # Derivative of the loss wrt the convolution filter
                dl1_f1p = np.where(l0_conv>0,dl1,0)
                dW1 = np.array([[(lt0[alpha:+alpha + image_size, beta:beta + image_size] * dl1_f1p).sum()
                                 for beta in range(K)] \
                                for alpha in range(K)])
                # Surprizingly this is slower even though my code is not vectorized
                # dW1=(view_as_windows(lt0,(image_size,image_size))*dl1_f1p[None,None,:,:]).sum(axis=(2,3))

            if update_W2:
                W2 += -eta * dW2
            if update_W1:
                W1 += -eta * dW1

            dl1[max_rows,max_cols]=0

        loss_averager_valid = averager()
        accuracy_averager_valid = averager()

        for X, y in zip(X_valid, y_valid):
            accuracy, loss = forward_pass(W1, W2, X, y)
            loss_averager_valid.send(loss)
            accuracy_averager_valid.send(accuracy)

        train_loss, train_accuracy, valid_loss, valid_accuracy = map(extract_averager_value, [
            train_loss,
            train_accuracy,
            loss_averager_valid,
            accuracy_averager_valid]
                                                                     )
        msg = 'Epoch {}: train loss {:.2f}, train acc {:.2f}, valid loss {:.2f}, valid acc {' \
              ':.2f}'.format(
            epoch + 1,
            train_loss,
            train_accuracy,
            valid_loss,
            valid_accuracy
            )
        print(msg)



# TRAIN THE MODEL
import time
W1, W2 = random_weights(42)
print('*'*25,'Training the model','*'*25)
t0=time.time()
train_model(W1,W2)
print(time.time()-t0)


# TRAIN THE MODEL with convolution weights frozen

W1, W2 = random_weights(42)
print('*'*25,'Training the model with convolution weights frozen','*'*25)
t0=time.time()
train_model(W1,W2, update_W1=False)
print(time.time()-t0)

# TRAIN THE MODEL with dense weights frozen

W1, W2 = random_weights(42)
print('*'*25,'Training the model with dense weights frozen','*'*25)
t0=time.time()
train_model(W1,W2,update_W2=False)
print(time.time()-t0)
