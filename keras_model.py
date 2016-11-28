from keras.models import Sequential
from keras.layers import Merge
from keras.layers import GRU
from keras.layers import Dense
from keras.layers.convolutional import Convolution1D
from keras.layers.wrappers import Bidirectional
from keras import backend
from preprocess import KerasIterator
from preprocess import TweetIterator
from preprocess import text2mat
from utils import loadPickle
import numpy as np
import os
from warnings import warn
from numpy.linalg import norm


mlb_file = './models/mlb.pickle'
if os.path.exists(mlb_file):
    mlb = loadPickle(mlb_file)
else:
    warn("{} doesn't exist - need this to generate labels for training: run `./preprocess.py --prepare input.txt` first")


# TODO need saving/loading models. There's already a function in utils to do this but haven't looked into the details


class Tweet2Vec:
    def __init__(self, model=None, char=True, chrd=True, word=True):
        '''
        Initialize stuff
        '''
        self.char = char
        self.chrd = chrd
        self.word = word
        charX, chrdX, wordX, y = next(TweetIterator(['this is to figure out input/output dimensions'], False, 'char_mat', 'chrd_mat', 'word_mat', 'label'))
        self.char_dim = charX.shape[1]
        self.chrd_dim = chrdX.shape[1]
        self.word_dim = wordX.shape[1]
        self.output_dim = y.shape[1]
        self.vector_cache_ = {}

        if model is None:
            self.gen_model()
        else:
            self.model = model

        # Former is using a merged model, latter if not
        if hasattr(self.model.layers[0], 'layers'):
            self.get_vec_ = backend.function([layer.input for layer in self.model.layers[0].layers], [self.model.layers[-2].output])
            num_expected = len([layer.input for layer in self.model.layers[0].layers])
        else:
            self.get_vec_ = backend.function([self.model.layers[0].input], [self.model.layers[-2].output])
            num_expected = 1

        num_actual = len([i for i in [char, chrd, word] if i])

        if num_expected != num_actual:
            warn("Number of expected inputs to your model ({}) and number of actual inputs ({}) are different. Either you need to change your model or change the Tweet2Vec() arguments".format(num_expected, num_actual))

    def gen_model(self):
        '''
        Build the model
        '''

        # word matrix branch
        word_branch = Sequential()
        word_branch.add(Bidirectional(GRU(50, input_dim=self.word_dim, return_sequences=False), input_shape=(None, self.word_dim)))
        # word_branch.add(Dense(20, activation='relu'))

        # char matrix branch
        char_branch = Sequential()
        char_branch.add(Convolution1D(100, 10, input_dim=self.char_dim))
        # char_branch.add(Bidirectional(GRU(50, input_dim=self.char_dim, return_sequences=False), input_shape=(None, self.char_dim)))
        char_branch.add(Bidirectional(GRU(50)))
        char_branch.add(Dense(50, activation='relu'))
        char_branch.add(Dense(50, activation='relu'))
        char_branch.add(Dense(50, activation='relu'))

        # merge models (concat outputs)
        self.model = Sequential()

        # The order here determines the order of your inputs. This must correspond to the standard (char, chrd, word) order.
        merged = Merge([char_branch, word_branch], mode='concat')
        self.model.add(merged)
        # self.model = char_branch

        # final hidden layer
        self.model.add(Dense(50, activation='relu'))

        # output layer
        self.model.add(Dense(self.output_dim, activation='softmax'))

        # loss function/optimizer
        self.model.compile(loss='categorical_crossentropy', optimizer='adam')

    def fit(self, source, batch_size=100, samples=None, num_epochs=1):
        '''
        Fit the model using data in source

        The inputs mean what they mean

        This will loop through source forever so it's okay if the numbers are more than your actual data

        For KerasIterator object, specify what matrices it should yield (char, chrd, word),
        these must correspond to what inputs the model expects.
        Note: the inputs will always feed to the model in the (char, chrd, word) order.
        '''

        keras_iterator = KerasIterator(source, batch_size, char=self.char, chrd=self.chrd, word=self.word)

        # If not specified, train on ALL data in source
        if samples is None:
            samples = len(keras_iterator.tweet_iterator)
        self.fit_data = self.model.fit_generator(keras_iterator, samples, num_epochs, verbose=1, nb_worker=2, pickle_safe=True)

    def evaluate(self, source):
        '''
        Prints the loss on tweets in source
        '''
        keras_iterator = KerasIterator(source, 1, char=self.char, chrd=self.chrd, word=self.word)
        num_samples = len(keras_iterator.tweet_iterator)
        loss = self.model.evaluate_generator(keras_iterator, num_samples)
        print("\nLoss on the {} samples in {} is: {}\n".format(num_samples, source, loss))

    def predict_hashtags(self, source, num_to_validate=None, num_best=1):
        '''
        Prints the `num_best` top predicted hashtags for `num_to_validate` lines in source
        '''

        raw = TweetIterator(source, True, 'raw_tweet')

        # If not specified, run on ALL tweets in source
        if num_to_validate is None:
            num_to_validate = len(raw)

        x = self.model.predict_generator(KerasIterator(source, 1, char=self.char, chrd=self.chrd, word=self.word), num_to_validate)

        for i, r in zip(x, raw):
            # goes through the highest prediction values and outputs
            if num_best > 1:
                best = i.argsort()[-num_best:][::-1]
            else:
                best = [i.argmax()]

            print("\nTweet: {}".format(r))
            best_hashtags = []
            for b in best:
                label = np.zeros((1, i.shape[0]))
                label[0, b] = 1
                predicted_hashtag = mlb.inverse_transform(label)[0][0]
                best_hashtags.append(predicted_hashtag)
            print("Predicted hashtags: {}\n".format(', '.join(best_hashtags)))

    def __getitem__(self, tweet):
        '''
        Gets the vector for tweet like the word2vec api
        e.g.

            tweet2vec['Raw text of the tweet']

        will return the vector.

        Also works on lists of tweets
        (in fact, this is the recommended way if you are getting lots of vectors
        because it seems to be just as slow getting one vector as it is many)

        caches vectors, so if you ask for them again it will happen in O(1) time
        '''
        if type(tweet) == str:
            tweet = [tweet]

        not_cached = [t for t in tweet if t not in self.vector_cache_]

        if not_cached:
            mats_in = []
            if self.char:
                charX = []
                for t in not_cached:
                    charX.append(text2mat(t, 'char'))
                mats_in.append(np.stack(charX))
            if self.chrd:
                chrdX = []
                for t in not_cached:
                    chrdX.append(text2mat(t, 'chrd'))
                mats_in.append(np.stack(chrdX))
            if self.word:
                wordX = []
                for t in not_cached:
                    wordX.append(text2mat(t, 'word'))
                mats_in.append(np.stack(wordX))
            not_cached_vectors = self.get_vec_(mats_in)[0]
            for t, v in zip(not_cached, not_cached_vectors):
                norm_v = norm(v)
                if norm_v == 0:
                    norm_v = 1
                self.vector_cache_[t] = v / norm(v)

        return np.array([self.vector_cache_[t] for t in tweet])

    def most_similar(self, tweet, source, batch_size=1000):
        '''
        Iterates through `source` and finds the line with the highest cosine
        similarity to `tweet`
        '''

        best_d = -1
        best_t = ''
        target_v = self[tweet]

        batch = []
        i = 0
        for t in TweetIterator(source, False, 'raw_tweet'):
            batch.append(t)
            i += 1
            if i == batch_size:
                dists = np.dot(self[batch], target_v.T)
                best_i = np.argmax(dists)
                d = dists[best_i]
                t = batch[best_i]
                if d > best_d:
                    best_t = t
                    best_d = d
                i = 0
                batch = []
        if batch:
            dists = np.dot(self[batch], target_v.T)
            best_i = np.argmax(dists)
            d = dists[best_i]
            t = batch[best_i]
            if d > best_d:
                best_t = t
                best_d = d

        return best_t, best_d

    def most_similar_test(self, source1, source2, num_test=10):
        '''
        Another "sanity check"

        Picks a random tweet in source1 and finds the closest tweet in source2 to it
        Does so `num_test` times

        Ideally there is no overlap between the two sources
        '''

        ti = TweetIterator(source1, False, 'raw_tweet')
        for _ in range(num_test):
            t1 = ti.get_random()
            t2, d = self.most_similar(t1, source2)
            print("\nOriginal tweet: {}\nClosest tweet: {}\nDistance: {}\n".format(t1, t2, d))


if __name__ == '__main__':

    tweet2vec = Tweet2Vec(char=True, chrd=False, word=True)

    train = './data/train.csv'
    test = './data/test.csv'

    # samples=None (the default) will train on all input data
    tweet2vec.fit(train, samples=1000)
    tweet2vec.evaluate(test)
    tweet2vec.most_similar_test(train, './data/sample.csv')
