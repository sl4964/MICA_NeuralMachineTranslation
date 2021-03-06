# -*- coding: utf-8 -*-

import random
import time
import math
import argparse
import torch
import torch.nn as nn
from torch.autograd import Variable
from torch import optim
import torch.nn.functional as F
from comet_ml import Experiment
import re
from nltk.translate import bleu_score

"""
Created on Wed Nov  8 22:31:02 2017

@author: eduardofierro
"""

######## File params ########

parser = argparse.ArgumentParser()
parser.add_argument('--MIN_LENGTH_INPUT', type=int, default=5, help='Min Length of sequence (Input side)')
parser.add_argument('--MAX_LENGTH_INPUT', type=int, default=200, help='Max Length of sequence (Input side)')
parser.add_argument('--MIN_LENGTH_TARGET', type=int, default=5, help='Min Length of sequence (Output side)')
parser.add_argument('--MAX_LENGTH_TARGET', type=int, default=200, help='Max Length of sequence (Output side)')
parser.add_argument('--lang1', type=str, default="en", help='Input Language')
parser.add_argument('--lang2', type=str, default="fr", help='Target Language')
parser.add_argument('--use_cuda', action='store_true', help='IF USE CUDA (Default == False)')
parser.add_argument('--teacher_forcing_ratio', type=float, default=0.5, help='Teacher forcing ratio for encoder')
parser.add_argument('--hidden_size', type=int, default=256, help='Size of hidden layer')
parser.add_argument('--num_epochs', type=int, default=3000, help='Number of epochs')
parser.add_argument('--learning_rate_decoder', type=float, default=0.001, help='Learning rate for decoder')
parser.add_argument('--learning_rate_encoder', type=float, default=0.001, help='Learning rate for encoder')
parser.add_argument('--n_layers', type=int, default=1, help='Number of layers (for both, encoder and decoder)')
parser.add_argument('--dropout_dec_p', type=float, default=0.1, help='Dropout (%) in the decoder')
parser.add_argument('--model_type', type=str, default="seq2seq", help='Model type (and ending of files)')
parser.add_argument('--main_data_dir', type=str, default= "/Users/eduardofierro/Google Drive/TercerSemetre/NLP/ProjectOwn/Data/Model_ready/", help='Directory where data is saved (in folders tain/dev/test)')
parser.add_argument('--out_dir', type=str, default="", help="Directory to save the models state dict (No default)")
parser.add_argument('--optimizer', type=str, default="Adam", help="Optimizer (Adam vs SGD). Default: Adam")
parser.add_argument('--kmax', type=int, default=10, help="Beam search Topk to search")
parser.add_argument('--criterion', type=str, default="NLLLoss", help="Beam search Topk to search")
opt = parser.parse_args()
print(opt)

######## Comet ML ########

experiment = Experiment(api_key="00Z9vIf4wOLZ0yrqzdwHqttv4", log_code=True)
hyper_params = vars(opt)
experiment.log_multiple_params(hyper_params)

#################################
# Languages classes and imports #
#################################

PAD_token = 0
SOS_token = 1
EOS_token = 2
UNK_token = 3

class Lang:
    def __init__(self, name):
        self.name = name
        self.trimmed = False
        self.word2index = {}
        self.word2count = {}
        self.index2word = {0: "PAD", 1: "SOS", 2: "EOS", 3: "UNK"}
        self.n_words = 4 # Count default tokens

    def index_words(self, sentence):
        for word in sentence.split(' '):
            self.index_word(word)

    def index_word(self, word):
        if word not in self.word2index:
            self.word2index[word] = self.n_words
            self.word2count[word] = 1
            self.index2word[self.n_words] = word
            self.n_words += 1
        else:
            self.word2count[word] += 1

    # Remove words below a certain count threshold
    def trim(self, min_count):
        if self.trimmed: return
        self.trimmed = True
        
        keep_words = []
        
        for k, v in self.word2count.items():
            if v >= min_count:
                keep_words.append(k)

        print('keep_words %s / %s = %.4f' % (
            len(keep_words), len(self.word2index), len(keep_words) / len(self.word2index)
        ))

        # Reinitialize dictionaries
        self.word2index = {}
        self.word2count = {}
        self.index2word = {0: "PAD", 1: "SOS", 2: "EOS"}
        self.n_words = 3 # Count default tokens

        for word in keep_words:
            self.index_word(word)

def read_langs(lang1, lang2, set_type="train", term="txt", reverse=False):
    print("Reading lines...")

    # Read the file and split into lines
    if set_type == "train":
        filename = '%s/train/%s-%s.%s' % (opt.main_data_dir, lang1, lang2, term)
    elif set_type == "dev":
        filename = '%s/dev/%s-%s.%s' % (opt.main_data_dir, lang1, lang2, term)
    elif set_type == "valid":
        filename = '%s/dev/%s-%s.%s' % (opt.main_data_dir, lang1, lang2, term)
    elif set_type == "tst2010":
        filename = '%s/test/%s-%s.tst2010-%s' % (opt.main_data_dir, lang1, lang2, term)
    elif set_type == "tst2011":
        filename = '%s/test/%s-%s.tst2011-%s' % (opt.main_data_dir, lang1, lang2, term)
    elif set_type == "tst2012":
        filename = '%s/test/%s-%s.tst2012-%s' % (opt.main_data_dir, lang1, lang2, term)
    elif set_type == "tst2013":
        filename = '%s/test/%s-%s.tst2013-%s' % (opt.main_data_dir, lang1, lang2, term)
    elif set_type == "tst2014":
        filename = '%s/test/%s-%s.tst2014-%s' % (opt.main_data_dir, lang1, lang2, term)  
    else:
        raise ValueError("set_type not found. Check data folder options")
        
        
    # lines contains the data in form of a list 
    lines = open(filename).read().strip().split('\n')

    # Split every line into pairs
    pairs = [[s for s in l.split('\t')] for l in lines]

    # Reverse pairs, make Lang instances
    if reverse:
        pairs = [list(reversed(p)) for p in pairs]
        input_lang = Lang(lang2)
        output_lang = Lang(lang1)
    else:
        input_lang = Lang(lang1)
        output_lang = Lang(lang2)

    return input_lang, output_lang, pairs


def filterPair(p, min_length_input, min_length_target, max_length_input, max_length_target):
    return len(p[0].split(' ')) > min_length_input and \
        len(p[1].split(' ')) > min_length_target and \
        len(p[0].split(' ')) < max_length_input and \
        len(p[1].split(' ')) < max_length_target

def filterPairs(pairs, min_length_input, min_length_target, max_length_input, max_length_target):
    return [pair for pair in pairs if filterPair(pair, min_length_input, min_length_target, max_length_input, max_length_target)]


def prepare_data(lang1_name, lang2_name, reverse=False, set_type="train"):

    # Get the source and target language class objects and the pairs (x_t, y_t)
    input_lang, output_lang, pairs = read_langs(lang1_name, lang2_name, set_type=set_type,  term=opt.model_type, reverse=reverse)
    print("Read %d sentence pairs" % len(pairs))
 
    pairs = filterPairs(pairs, opt.MIN_LENGTH_INPUT, opt.MIN_LENGTH_TARGET, opt.MAX_LENGTH_INPUT, opt.MAX_LENGTH_TARGET)
    print("Filtered to %d pairs" % len(pairs))

    print("Indexing words...")
    for pair in pairs:
        input_lang.index_words(pair[0])
        output_lang.index_words(pair[1])
    
    print('Indexed %d words in input language, %d words in output' % (input_lang.n_words, output_lang.n_words))
    return input_lang, output_lang, pairs
    
def indexes_from_sentence(lang, sentence):
    
    try: 
        val = [lang.word2index[word] for word in sentence.split(' ')]
    except KeyError: 
        # Do it individually. Means one word is not on dictionary: 
        val = []
        for word in sentence.split(' '):
            try: 
                indexed = lang.word2index[word]
                val.append(indexed)
            except KeyError:
                val.append(3)
                
    return val

def variable_from_sentence(lang, sentence):
    indexes = indexes_from_sentence(lang, sentence)
    indexes.append(EOS_token)
    var = Variable(torch.LongTensor(indexes).view(-1, 1))
    if opt.use_cuda: 
        var = var.cuda()
    return var

def variables_from_pair(pair):
    input_variable = variable_from_sentence(input_lang, pair[0])
    target_variable = variable_from_sentence(output_lang, pair[1])
    return (input_variable, target_variable)
    
################################
# Main model encoder - decoder #
################################  
    
# Both classes form Lab - Week 9 (Lab8)   

class EncoderRNN(nn.Module):
    def __init__(self, input_size, hidden_size, n_layers=1):
        super(EncoderRNN, self).__init__()
        self.n_layers = n_layers
        self.hidden_size = hidden_size
        self.embedding = nn.Embedding(input_size, hidden_size)
        self.gru = nn.GRU(hidden_size, hidden_size)

    def forward(self, input, hidden):
        embedded = self.embedding(input).view(1, 1, -1)
        output = embedded
        for i in range(self.n_layers):
            output, hidden = self.gru(output, hidden)
        return output, hidden

    def initHidden(self):
        result = Variable(torch.zeros(1, 1, self.hidden_size))
        if opt.use_cuda:
            return result.cuda()
        else:
            return result
        
class AttnDecoderRNN(nn.Module):
    def __init__(self, hidden_size, output_size, n_layers=1, dropout_p=0.1, max_length=opt.MAX_LENGTH_TARGET):
        super(AttnDecoderRNN, self).__init__()
        self.hidden_size = hidden_size
        self.output_size = output_size
        self.n_layers = n_layers
        self.dropout_p = dropout_p
        self.max_length = max_length

        self.embedding = nn.Embedding(self.output_size, self.hidden_size)
        self.attn = nn.Linear(self.hidden_size * 2, self.max_length)
        self.attn_combine = nn.Linear(self.hidden_size * 2, self.hidden_size)
        self.dropout = nn.Dropout(self.dropout_p)
        self.gru = nn.GRU(self.hidden_size, self.hidden_size)
        self.out = nn.Linear(self.hidden_size, self.output_size)

    def forward(self, input, hidden, encoder_output, encoder_outputs):
        embedded = self.embedding(input).view(1, 1, -1)
        embedded = self.dropout(embedded)

        attn_weights = F.softmax(
            self.attn(torch.cat((embedded[0], hidden[0]), 1)))
        attn_applied = torch.bmm(attn_weights.unsqueeze(0),
                                 encoder_outputs.unsqueeze(0))

        output = torch.cat((embedded[0], attn_applied[0]), 1)
        output = self.attn_combine(output).unsqueeze(0)

        for i in range(self.n_layers):
            output = F.relu(output)
            output, hidden = self.gru(output, hidden)

        output = F.log_softmax(self.out(output[0]))
        return output, hidden, attn_weights

    def initHidden(self):
        result = Variable(torch.zeros(1, 1, self.hidden_size))
        if opt.use_cuda:
            return result.cuda()
        else:
            return result    

##############
# Evaluation #
##############

def update_dictionary(target_sequence, topv, topi, key, dec_hidden, decoder_attns):
    if len(target_sequence) == 0:
        for i in range(len(topi)):
            target_sequence.update({str(topi[i]) : [topv[i], dec_hidden, decoder_attns] })
    else:
        prev_val = target_sequence[key][0]
        for i in range(len(topi)):
            target_sequence.update({key+"-"+str(topi[i]) : [topv[i]*prev_val, dec_hidden, decoder_attns] })
        del[target_sequence[key]]
        
def get_seq_through_beam_search(max_length, decoder, decoder_input, decoder_hidden, decoder_attentions, encoder_output, encoder_outputs, kmax ):
    target_sequence = dict()
    
    # Run through decoder
    for di in range(max_length):
        
        if di == 0:
            decoder_output, decoder_hidden, decoder_attention = decoder( decoder_input, decoder_hidden, encoder_output, encoder_outputs )
            topv, topi = decoder_output.data.topk(kmax)
            topv = topv[0]
            topi = topi[0]
            decoder_attentions[di,:decoder_attention.size(1)] += decoder_attention.data
            update_dictionary(target_sequence, topv, topi, None, decoder_hidden, decoder_attentions)
        else:
            temp = target_sequence.copy()
            keys = list(temp.keys())
            for i in range(len(keys)):
                inp = int(keys[i].split("-")[-1] if len(keys[i]) > 1 else keys[i])
                if inp != EOS_token:
                    dec_input = Variable(torch.LongTensor([inp]))
                    dec_input = dec_input.cuda() if opt.use_cuda else dec_input
                    decoder_output, dec_hidden, decoder_attention = decoder( dec_input, temp[keys[i]][1], encoder_output, encoder_outputs)
                    topv, topi = decoder_output.data.topk(kmax)
                    topv = topv[0]
                    topi = topi[0]
                    dec_attns = temp[keys[i]][2]
                    dec_attns[di,:decoder_attention.size(1)] += decoder_attention.data
                    update_dictionary(target_sequence, topv, topi, keys[i], dec_hidden, dec_attns)
        
        # Sort the target_Sequence dictionary to keep top k sequences only
        target_sequence = dict(sorted(target_sequence.items(), key=lambda x: x[1][0], reverse=True)[:kmax])
     
    # Get the sequence, decoder_attentions with maximum probability
    pair = sorted(target_sequence.items(), key=lambda x: x[1][0], reverse=True)[:1][0]
    seq = pair[0]
    decoder_attentions = pair[1][2]
    
    # Get the decoded words:
    decoded_words_indices = seq.split("-")
    decoded_words = [output_lang.index2word[int(i)] for i in decoded_words_indices]
    if int(decoded_words_indices[-1]) != EOS_token:
        decoded_words.append('<EOS>')
    
    return decoded_words, decoder_attentions

def evaluate(input_lang, output_lang, encoder, decoder, sentence, max_length, kmax = opt.kmax):
    """
    Function that generate translation.
    First, feed the source sentence into the encoder and obtain the hidden states from encoder.
    Secondly, feed the hidden states into the decoder and unfold the outputs from the decoder.
    Lastly, for each outputs from the decoder, collect the corresponding words in the target language's vocabulary.
    And collect the attention for each output words.
    @param encoder: the encoder network
    @param decoder: the decoder network
    @param sentence: string, a sentence in source language to be translated
    @param max_length: the max # of words that the decoder can return
    
    @output decoded_words: a list of words in target language
    @output decoder_attentions: a list of vector, each of which sums up to 1.0
    """
    
    # Set to not-training mode to disable dropout
    encoder.train(False)
    decoder.train(False)    

    # process input sentence
    input_variable = variable_from_sentence(input_lang, sentence)
    input_length = input_variable.size()[0]
    
    # encode the source lanugage
    encoder_hidden = encoder.initHidden()
    encoder_outputs = Variable(torch.zeros(max_length, encoder.hidden_size))
    encoder_outputs = encoder_outputs.cuda() if opt.use_cuda else encoder_outputs
    for ei in range(input_length):
        encoder_output, encoder_hidden = encoder(input_variable[ei],
                                                 encoder_hidden)
        encoder_outputs[ei] = encoder_outputs[ei] + encoder_output[0][0]
    
    # decode the context vector
    decoder_hidden = encoder_hidden # decoder starts from the last encoding sentence
    decoder_input = Variable(torch.LongTensor([[SOS_token]]))  # SOS
    decoder_input = decoder_input.cuda() if opt.use_cuda else decoder_input
    # output of this function
    decoded_words = []
    decoder_attentions = torch.zeros(max_length, max_length)
    decoder_attentions = decoder_attentions.cuda() if opt.use_cuda else decoder_attentions
    
    decoded_words, decoder_attentions = get_seq_through_beam_search(max_length, decoder, decoder_input, decoder_hidden, 
                                                                    decoder_attentions, encoder_output, encoder_outputs, kmax)
    
    # Set back to training mode
    encoder.train(True)
    decoder.train(True)    
    
    return decoded_words, decoder_attentions[:len(decoded_words)+1, :len(encoder_outputs)]

def evaluateRandomly(input_lang, output_lang, encoder, decoder, max_length, n=5):
    """
    Randomly select a English sentence from the dataset and try to produce its French translation.
    Note that you need a correct implementation of evaluate() in order to make this function work.
    """
    for i in range(n):
        pair = random.choice(pairs)
        print('>', pair[0])
        print('=', pair[1])
        output_words, attentions = evaluate(input_lang, output_lang, encoder, decoder, pair[0], max_length)
        output_sentence = ' '.join(output_words)
        print('<', output_sentence)
        print('')
        
def eval_single(input_lang, output_lang, encoder, decoder, string, max_length=opt.MAX_LENGTH_TARGET):
    
    words, tensor = evaluate(input_lang, output_lang, encoder, decoder, string, max_length=opt.MAX_LENGTH_TARGET)
    words = ' '.join(words)
    words = re.sub(' <EOS>', '', words)
    return(words)
    
def evaluate_dev(input_lang, output_lang, encoder, decoder, list_strings, 
                 max_length=opt.MAX_LENGTH_TARGET):
    
    output = [eval_single(input_lang, output_lang, encoder, decoder, x[0], max_length) for x in list_strings]
    
    return(output)
            
############################
# Training & training loop #
############################
            
def asMinutes(s):
    m = math.floor(s / 60)
    s -= m * 60
    return '%dm %ds' % (m, s)

def timeSince(since, percent):
    now = time.time()
    s = now - since
    es = s / (percent)
    rs = es - s
    return '%s (- %s)' % (asMinutes(s), asMinutes(rs))
    
def train(input_variable, target_variable, encoder, decoder, 
            encoder_optimizer, decoder_optimizer, criterion, max_length=opt.MAX_LENGTH_TARGET):
    
    encoder_hidden = encoder.initHidden()

    encoder_optimizer.zero_grad()
    decoder_optimizer.zero_grad()

    input_length = input_variable.size()[0]
    target_length = target_variable.size()[0]
    
    encoder_outputs = Variable(torch.zeros(max_length, encoder.hidden_size))
    encoder_outputs = encoder_outputs.cuda() if opt.use_cuda else encoder_outputs
   
    loss = 0

    for ei in range(input_length):
        encoder_output, encoder_hidden = encoder(
            input_variable[ei], encoder_hidden)
        encoder_outputs[ei] = encoder_output[0][0]

    decoder_input = Variable(torch.LongTensor([[SOS_token]]))
    decoder_input = decoder_input.cuda() if opt.use_cuda else decoder_input
    
    decoder_hidden = encoder_hidden

    use_teacher_forcing = True if random.random() < opt.teacher_forcing_ratio else False

    if use_teacher_forcing:
        # Teacher forcing: Feed the target as the next input
        for di in range(target_length):
            decoder_output, decoder_hidden, decoder_attention = decoder(
                decoder_input, decoder_hidden, encoder_output, encoder_outputs)
            loss += criterion(decoder_output, target_variable[di])
            decoder_input = target_variable[di]  # Teacher forcing

    else:
        # Without teacher forcing: use its own predictions as the next input
        for di in range(target_length):
            decoder_output, decoder_hidden, decoder_attention = decoder(
                decoder_input, decoder_hidden, encoder_output, encoder_outputs)
            topv, topi = decoder_output.data.topk(1)
            ni = topi[0][0]
            
            decoder_input = Variable(torch.LongTensor([[ni]]))
            decoder_input = decoder_input.cuda() if opt.use_cuda else decoder_input
            
            loss += criterion(decoder_output, target_variable[di])
            if ni == EOS_token:
                break

    loss.backward()

    encoder_optimizer.step()
    decoder_optimizer.step()

    return loss.data[0] / target_length
    
def trainIters(input_lang, output_lang, encoder, decoder, num_epochs, pairs, pairs_eval, loss_criterion,
               learning_rate_encoder=opt.learning_rate_encoder, learning_rate_decoder=opt.learning_rate_decoder, 
               print_every=500):
    
    start = time.time()
    
    # Optimizers = ADAM in Chung, Cho and Bengio 2016
    if opt.optimizer == "Adam":
        encoder_optimizer = optim.Adam(encoder.parameters(), lr=learning_rate_encoder)
        decoder_optimizer = optim.Adam(decoder.parameters(), lr=learning_rate_decoder)
    elif opt.optimizer == "SGD":
        encoder_optimizer = optim.SGD(encoder.parameters(), lr=learning_rate_encoder)
        decoder_optimizer = optim.SGD(decoder.parameters(), lr=learning_rate_decoder)  
    else: 
        raise ValueError('Optimizer options not found: Select SGD or Adam') 
                      
    criterion = loss_criterion
    
    iter_tot = 1
    n_iters = num_epochs * len(pairs)
    loss_avg = 0
    print_loss_avg = 0    
    
    for epoch in range(1, num_epochs + 1):
        for z in range(0, len(pairs)):
            
            training_pair = variables_from_pair(pairs[z])
            input_variable = training_pair[0]
            target_variable = training_pair[1]
 
            loss = train(input_variable, target_variable, encoder,
                         decoder, encoder_optimizer, decoder_optimizer, criterion)
            loss_avg += loss
            print_loss_avg += 1
            if iter_tot % print_every == 0:
                print_loss_avg = loss_avg / print_loss_avg
                print('%s (%d %d%%) %.4f' % (timeSince(start, iter_tot / n_iters),
                                             iter_tot, iter_tot / n_iters * 100, print_loss_avg))
                experiment.log_metric("Train loss", loss)
                loss_avg = 0
                print_loss_avg = 0
                
            iter_tot += 1
        
        torch.save(encoder.state_dict(), "{}/saved_encoder_{}.pth".format(opt.out_dir, epoch))
        torch.save(decoder.state_dict(), "{}/saved_decoder_{}.pth".format(opt.out_dir, epoch))
        
        prediction = evaluate_dev(input_lang, output_lang, encoder, decoder, pairs_eval)
        target_eval = [x[1] for x in pairs_eval]
        bleu_corpus = bleu_score.corpus_bleu(target_eval, prediction)
        experiment.log_metric("BLEU score", bleu_corpus)
        print("NLTK's Blue score: {} at epoch {}".format(round(bleu_corpus, 2), epoch))
        evaluateRandomly(input_lang, output_lang, encoder1, attn_decoder1, 
                         max_length=opt.MAX_LENGTH_TARGET, n=5) # This are from train
            
        
#########
# Train #
#########

input_lang, output_lang, pairs = prepare_data(opt.lang1, opt.lang2, set_type="train")
input_lang_dev, output_lang_dev, pairs_dev = prepare_data(opt.lang1, opt.lang2, set_type="dev")

encoder1 = EncoderRNN(input_lang.n_words, opt.hidden_size)
attn_decoder1 = AttnDecoderRNN(opt.hidden_size, output_lang.n_words,
                               opt.n_layers, dropout_p=opt.dropout_dec_p, max_length=opt.MAX_LENGTH_TARGET)

if opt.use_cuda:
    encoder1 = encoder1.cuda()
    attn_decoder1 = attn_decoder1.cuda()

if opt.criterion == "NLLLoss": 
    lcriterion = nn.NLLLoss()
elif opt.criterion == "CrossEntropyLoss": 
    lcriterion = nn.CrossEntropyLoss()
else:
    raise ValueError("criterion nof found")

trainIters(input_lang, output_lang, encoder1, attn_decoder1, num_epochs=opt.num_epochs, 
           pairs=pairs, pairs_eval=pairs_dev, loss_criterion=lcriterion,
           print_every=500)        
