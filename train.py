from __future__ import print_function
import os
import sys
import random
import time
import argparse
import tensorflow as tf
import numpy as np
from embvec import EmbVec
from config import Config
from model import Model
from input import Input
from token_eval  import TokenEval
from chunk_eval  import ChunkEval
from progbar import Progbar
from early_stopping import EarlyStopping

def train_step(sess, model, config, data, summary_op, summary_writer):
    start_time = time.time()
    runopts = tf.RunOptions(report_tensor_allocations_upon_oom=True)
    prog = Progbar(target=data.num_batches)
    iterator = data.dataset.make_initializable_iterator()
    next_element = iterator.get_next()
    sess.run(iterator.initializer)
    for idx in range(data.num_batches):
        try:
            dataset = sess.run(next_element)
        except tf.errors.OutOfRangeError:
            break
        config.is_training = True
        feed_dict={model.input_data_pos_ids: dataset['pos_ids'],
                   model.input_data_chk_ids: dataset['chk_ids'],
                   model.output_data: dataset['tags'],
                   model.is_train: config.is_training,
                   model.sentence_length: data.max_sentence_length}
        feed_dict[model.input_data_word_ids] = dataset['word_ids']
        feed_dict[model.input_data_wordchr_ids] = dataset['wordchr_ids']
        if 'elmo' in config.emb_class:
            feed_dict[model.elmo_input_data_wordchr_ids] = dataset['elmo_wordchr_ids']
        if 'bert' in config.emb_class:
            feed_dict[model.bert_input_data_token_ids] = dataset['bert_token_ids']
            feed_dict[model.bert_input_data_token_masks] = dataset['bert_token_masks']
            feed_dict[model.bert_input_data_segment_ids] = dataset['bert_segment_ids']
            if 'elmo' in config.emb_class:
                feed_dict[model.bert_input_data_elmo_indices] = dataset['bert_elmo_indices']
        if 'bert' in config.emb_class:
            step, summaries, _, loss, accuracy, f1, learning_rate, bert_embeddings = \
                   sess.run([model.global_step, summary_op, model.train_op, \
                             model.loss, model.accuracy, model.f1, model.learning_rate, model.bert_embeddings], feed_dict=feed_dict, options=runopts)
            if idx == 0:
                tf.logging.debug('# bert_token_ids')
                t = dataset['bert_token_ids'][:3]
                tf.logging.debug(' '.join([str(x) for x in np.shape(t)]))
                tf.logging.debug(' '.join([str(x) for x in t]))
                tf.logging.debug('# bert_token_masks')
                t = dataset['bert_token_masks'][:3]
                tf.logging.debug(' '.join([str(x) for x in np.shape(t)]))
                tf.logging.debug(' '.join([str(x) for x in t]))
                tf.logging.debug('# bert_embedding')
                t = bert_embeddings[:3]
                tf.logging.debug(' '.join([str(x) for x in np.shape(t)]))
                tf.logging.debug(' '.join([str(x) for x in t]))
        else:
            step, summaries, _, loss, accuracy, f1, learning_rate = \
                   sess.run([model.global_step, summary_op, model.train_op, \
                             model.loss, model.accuracy, model.f1, model.learning_rate], feed_dict=feed_dict, options=runopts)

        summary_writer.add_summary(summaries, step)
        prog.update(idx + 1,
                    [('step', step),
                     ('train loss', loss),
                     ('train accuracy', accuracy),
                     ('train f1', f1),
                     ('lr(invalid if use_bert_optimization)', learning_rate)])
    duration_time = time.time() - start_time
    out = '\nduration_time : ' + str(duration_time) + ' sec for this epoch'
    tf.logging.debug(out)

def np_concat(sum_var, var):
    if sum_var is not None: sum_var = np.concatenate((sum_var, var), axis=0)
    else: sum_var = var
    return sum_var

def dev_step(sess, model, config, data, summary_writer, epoch):
    sum_loss = 0.0
    sum_accuracy = 0.0
    sum_f1 = 0.0
    sum_output_indices = None
    sum_logits_indices = None
    sum_sentence_lengths = None
    trans_params = None
    global_step = 0
    prog = Progbar(target=data.num_batches)
    iterator = data.dataset.make_initializable_iterator()
    next_element = iterator.get_next()
    sess.run(iterator.initializer)
    # evaluate on dev data sliced by batch_size to prevent OOM(Out Of Memory).
    for idx in range(data.num_batches):
        try:
            dataset = sess.run(next_element)
        except tf.errors.OutOfRangeError:
            break
        config.is_training = False
        feed_dict={model.input_data_pos_ids: dataset['pos_ids'],
                   model.input_data_chk_ids: dataset['chk_ids'],
                   model.output_data: dataset['tags'],
                   model.is_train: config.is_training,
                   model.sentence_length: data.max_sentence_length}
        feed_dict[model.input_data_word_ids] = dataset['word_ids']
        feed_dict[model.input_data_wordchr_ids] = dataset['wordchr_ids']
        if 'elmo' in config.emb_class:
            feed_dict[model.elmo_input_data_wordchr_ids] = dataset['elmo_wordchr_ids']
        if 'bert' in config.emb_class:
            feed_dict[model.bert_input_data_token_ids] = dataset['bert_token_ids']
            feed_dict[model.bert_input_data_token_masks] = dataset['bert_token_masks']
            feed_dict[model.bert_input_data_segment_ids] = dataset['bert_segment_ids']
            if 'elmo' in config.emb_class:
                feed_dict[model.bert_input_data_elmo_indices] = dataset['bert_elmo_indices']
        global_step, logits_indices, sentence_lengths, loss, accuracy, f1 = \
                 sess.run([model.global_step, model.logits_indices, model.sentence_lengths, \
                           model.loss, model.accuracy, model.f1], feed_dict=feed_dict)
        prog.update(idx + 1,
                    [('dev loss', loss),
                     ('dev accuracy', accuracy),
                     ('dev f1', f1)])
        sum_loss += loss
        sum_accuracy += accuracy
        sum_f1 += f1
        sum_output_indices = np_concat(sum_output_indices, np.argmax(dataset['tags'], 2))
        sum_logits_indices = np_concat(sum_logits_indices, logits_indices)
        sum_sentence_lengths = np_concat(sum_sentence_lengths, sentence_lengths)
        idx += 1
    avg_loss = sum_loss / data.num_batches
    avg_accuracy = sum_accuracy / data.num_batches
    avg_f1 = sum_f1 / data.num_batches
    tag_preds = config.logits_indices_to_tags_seq(sum_logits_indices, sum_sentence_lengths)
    tag_corrects = config.logits_indices_to_tags_seq(sum_output_indices, sum_sentence_lengths)
    tf.logging.debug('\n[epoch %s/%s] dev precision, recall, f1(token): ' % (epoch, config.epoch))
    token_f1, l_token_prec, l_token_rec, l_token_f1  = TokenEval.compute_f1(config.class_size, sum_logits_indices, sum_output_indices, sum_sentence_lengths)
    tf.logging.debug('[' + ' '.join([str(x) for x in l_token_prec]) + ']')
    tf.logging.debug('[' + ' '.join([str(x) for x in l_token_rec]) + ']')
    tf.logging.debug('[' + ' '.join([str(x) for x in l_token_f1]) + ']')
    chunk_prec, chunk_rec, chunk_f1 = ChunkEval.compute_f1(tag_preds, tag_corrects)
    tf.logging.debug('dev precision(chunk), recall(chunk), f1(chunk): %s, %s, %s' % (chunk_prec, chunk_rec, chunk_f1) + '(invalid for bert due to X tag)')

    # create summaries manually.
    summary_value = [tf.Summary.Value(tag='loss', simple_value=avg_loss),
                     tf.Summary.Value(tag='accuracy', simple_value=avg_accuracy),
                     tf.Summary.Value(tag='f1', simple_value=avg_f1),
                     tf.Summary.Value(tag='token_f1', simple_value=token_f1),
                     tf.Summary.Value(tag='chunk_f1', simple_value=chunk_f1)]
    summaries = tf.Summary(value=summary_value)
    summary_writer.add_summary(summaries, global_step)
    
    return token_f1, chunk_f1, avg_f1

def do_train(model, config, train_data, dev_data):
    session_conf = tf.ConfigProto(allow_soft_placement=True, log_device_placement=False)
    session_conf.gpu_options.allow_growth = True
    sess = tf.Session(config=session_conf)
    feed_dict = {model.wrd_embeddings_init: config.embvec.wrd_embeddings}
    sess.run(tf.global_variables_initializer(), feed_dict=feed_dict) # feed large embedding data
    sess.run(tf.local_variables_initializer()) # for tf_metrics
    saver = tf.train.Saver()
    if config.restore is not None:
        saver.restore(sess, config.restore)
        tf.logging.debug('model restored')

    # summary setting
    loss_summary = tf.summary.scalar('loss', model.loss)
    acc_summary = tf.summary.scalar('accuracy', model.accuracy)
    f1_summary = tf.summary.scalar('f1', model.f1)
    lr_summary = tf.summary.scalar('learning_rate', model.learning_rate)
    train_summary_op = tf.summary.merge([loss_summary, acc_summary, f1_summary, lr_summary])
    train_summary_dir = os.path.join(config.summary_dir, 'summaries', 'train')
    train_summary_writer = tf.summary.FileWriter(train_summary_dir, sess.graph)
    dev_summary_dir = os.path.join(config.summary_dir, 'summaries', 'dev')
    dev_summary_writer = tf.summary.FileWriter(dev_summary_dir, sess.graph)

    early_stopping = EarlyStopping(patience=10, measure='f1', verbose=1)
    max_token_f1 = 0
    max_chunk_f1 = 0
    max_avg_f1 = 0
    for e in range(config.epoch):
        train_step(sess, model, config, train_data, train_summary_op, train_summary_writer)
        token_f1, chunk_f1, avg_f1  = dev_step(sess, model, config, dev_data, dev_summary_writer, e)
        # early stopping
        if early_stopping.validate(token_f1, measure='f1'): break
        if token_f1 > max_token_f1 or (max_token_f1 - token_f1 < 0.0005 and chunk_f1 > max_chunk_f1):
            tf.logging.debug('new best f1 score! : %s' % token_f1)
            max_token_f1 = token_f1
            max_chunk_f1 = chunk_f1
            max_avg_f1 = avg_f1
            # save best model
            save_path = saver.save(sess, config.checkpoint_dir + '/' + 'ner_model')
            tf.logging.debug('max model saved in file: %s' % save_path)
            tf.train.write_graph(sess.graph, '.', config.checkpoint_dir + '/' + 'graph.pb', as_text=False)
            tf.train.write_graph(sess.graph, '.', config.checkpoint_dir + '/' + 'graph.pb_txt', as_text=True)
            early_stopping.reset(max_token_f1)
        early_stopping.status()
    sess.close()

def train(config):
    # build input data
    train_file = 'data/train.txt'
    dev_file = 'data/dev.txt'
    '''for KOR
    train_file = 'data/kor.train.txt'
    dev_file = 'data/kor.dev.txt'
    '''
    '''for CRZ
    train_file = 'data/cruise.train.txt.in'
    dev_file = 'data/cruise.dev.txt.in'
    '''
    train_data = Input(train_file, config, build_output=True, do_shuffle=True)
    dev_data = Input(dev_file, config, build_output=True)
    #train_data = Input(train_file, config, build_output=True, do_shuffle=True, reuse=True)
    #dev_data = Input(dev_file, config, build_output=True, reuse=True)
    tf.logging.debug('loading input data ... done')

    # set config after reading training data
    config.num_train_steps = int((train_data.num_examples / config.batch_size) * config.epoch)
    config.num_warmup_steps = config.num_warmup_epoch * int(train_data.num_examples / config.batch_size)
    if config.num_warmup_steps == 0: config.num_warmup_steps = 1 # prevent dividing by zero
    tf.logging.debug('config.num_train_steps = %s' % config.num_train_steps)
    tf.logging.debug('config.num_warmup_epoch = %s' % config.num_warmup_epoch)
    tf.logging.debug('config.num_warmup_steps = %s' % config.num_warmup_steps)

    # create model
    model = Model(config)

    # training
    do_train(model, config, train_data, dev_data)
        
if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--emb_path', type=str, help='path to word embedding vector + vocab(.pkl)', required=True)
    parser.add_argument('--wrd_dim', type=int, help='dimension of word embedding vector', required=True)
    parser.add_argument('--word_length', type=int, default=15, help='max word length')
    parser.add_argument('--batch_size', type=int, default=128, help='batch size of training')
    parser.add_argument('--epoch', type=int, default=50, help='number of epochs')
    parser.add_argument('--checkpoint_dir', type=str, default='./checkpoint', help='dir path to save model(ex, ./checkpoint)')
    parser.add_argument('--restore', type=str, default=None, help='path to saved model(ex, ./checkpoint/ner_model)')
    parser.add_argument('--summary_dir', type=str, default='./runs', help='path to save summary(ex, ./runs)')

    args = parser.parse_args()
    tf.logging.set_verbosity(tf.logging.DEBUG)

    config = Config(args, is_training=True, emb_class='glove', use_crf=True)
    train(config)
