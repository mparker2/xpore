import numpy
import h5py
import os
from tqdm import tqdm  # progress bar.
from collections import OrderedDict
import itertools

from ..utils import stats
from .gmm import GMM


def get_dummies(x):
    X = []
    labels = sorted(list(set(x)))
    for label in labels:
        X += [x == label]
    # labels = [label.encode('UTF-8') for label in labels]
    return numpy.array(X).T, labels


def load_data(idx, data_dict, condition_names, run_names, min_count=30, max_count=3000,pooling=False):
    """
    Parameters
    ----------
    data_dict: dict of ...
        Data for a gene 
    """
    position_kmer_pairs = []
    for run_name in run_names: # data_dict[run_name][idx][position][kmer]
        pairs = []
        for pos in data_dict[run_name][idx].keys():
            for kmer in data_dict[run_name][idx][pos].keys():
                pairs += [(pos,kmer)]                
        position_kmer_pairs += [pairs]

    position_kmer_pairs = set(position_kmer_pairs[0]).intersection(*position_kmer_pairs)

    data = OrderedDict()
    for pos,kmer in position_kmer_pairs:  
        y, read_ids, condition_labels, run_labels = [], [], [], []
        for condition_name, run_name in zip(condition_names, run_names):
            norm_means = data_dict[run_name][idx][pos][kmer]['norm_means']
            n_reads = len(norm_means)
            y += norm_means
            read_ids += list(data_dict[run_name][idx][pos][kmer]['read_ids'][:])
            condition_labels += [condition_name]*n_reads
            run_labels += [run_name]*n_reads

        y = numpy.array(y)
        read_ids = numpy.array(read_ids)
        condition_labels = numpy.array(condition_labels)
        run_labels = numpy.array(run_labels)
        if (len(y) == 0) or (len(set(condition_labels)) != len(set(condition_names))) or ( (not pooling) and (len(set(run_labels)) != len(set(run_names)))):
            continue

        x, condition_names_dummies = get_dummies(condition_labels)
        r, run_names_dummies = get_dummies(run_labels)
        if pooling:
            if (x.sum(axis=0) < min_count).any() or (x.sum(axis=0) > max_count).any():
                continue

        else: 
            if (r.sum(axis=0) < min_count).any() or (r.sum(axis=0) > max_count).any():
                continue

        key = (idx, pos, kmer)

        data[key] = {'y': y, 'x': x, 'r': r, 'condition_names': condition_names_dummies, 'run_names': run_names_dummies, 'read_ids': read_ids, 'y_condition_names': condition_labels, 'y_run_names': run_labels}

    return data


def save_result_table(table, out_filepath):
    out_file = h5py.File(out_filepath, 'w')
    out_file['result'] = table  # Structured numpy array.
    out_file.close()


def save_models(models, model_filepath):  # per gene/transcript
    """
    Save model parameters.

    Parameters
    ----------
    models
        Learned models.
    model_filepath: str
        Path to save the models.
    """
    model_file = h5py.File(model_filepath, 'w')
    for model_key, model in models.items():  # tqdm(models.items()):
        idx, position, kmer = model_key

        position = str(position)
        if idx not in model_file:
            model_file.create_group(idx)
        model_file[idx].create_group(position)
        model_file[idx][position].attrs['kmer'] = kmer.encode('UTF-8')
        model_file[idx][position].create_group('info')
        for key, value in model.info.items():
            model_file[idx][position]['info'][key] = value

        model_file[idx][position].create_group('nodes')  # ['x','y','z','w','mu_tau'] => store only their params
        for node_name in model.nodes:
            model_file[idx][position]['nodes'].create_group(node_name)
            if model.nodes[node_name].params is None:
                continue
            for param_name, value in model.nodes[node_name].params.items():
                if param_name == 'group_names':
                    value = [val.encode('UTF-8') for val in value]
                model_file[idx][position]['nodes'][node_name][param_name] = value
            # if model.nodes[node_name].data is not None: # To be optional.
            #     model_file[idx][position]['nodes'][node_name]['data'] = model.nodes[node_name].data

    model_file.close()


def load_models(model_filepath):  # per gene/transcript
    """
    Construct a model and load model parameters.

    Parameters
    ----------
    model_filepath: str
        Path where the model is stored.

    Return
    ------
    models
        Models for each genomic position.
    """

    model_file = h5py.File(model_filepath, 'r')
    models = {}
    for idx in model_file:
        for position in tqdm(model_file[idx]):
            inits = {'info': None, 'nodes': {'x': {}, 'y': {}, 'w': {}, 'mu_tau': {}, 'z': {}}}
            kmer = model_file[idx][position].attrs['kmer']
            key = (idx, position, kmer)
            # for k in model_file[idx][position]['info']:
            #     inits['info'] = model_file[idx][position]['info'][k]
            for node_name, params in model_file[idx][position]['nodes'].items():
                for param_name, value in params.items():
                    inits['nodes'][node_name][param_name] = value[:]
                # for param_name, value in priors.items():
                #     inits['nodes'][node_name][param_name] = value[:]

            models[key] = GMM(inits=inits)

    model_file.close()

    return models  # {(idx,position,kmer): GMM obj}

def get_result_table_header(cond2run_dict):
    condition_names,run_names = get_ordered_condition_run_names(cond2run_dict)
    ### stats headers
    stats_pairwise = []
    for cond1, cond2 in itertools.combinations(condition_names, 2):
        pair = '_vs_'.join((cond1, cond2))
        stats_pairwise += ['p_ws_%s' % pair, 'ws_mean_diff_%s' % pair, 'abs_z_score_%s' % pair]
    stats_one_vs_all = []
    for condition_name in condition_names:
        stats_one_vs_all += ['p_ws_%s_vs_all' % condition_name, 'ws_mean_diff_%s_vs_all' % condition_name, 'abs_z_score_%s_vs_all' % condition_name]

    header = ['idx', 'position', 'kmer', 'mu_min', 'mu_max', 'sigma2_min', 'sigma2_max']
    header += ['p_overlap']
    header += ['x_x1', 'y_x1', 'x_x2', 'y_x2']
    for run_name in run_names:
        header += ['w_min_%s' % run_name]
    for run_name in run_names:
        header += ['coverage_%s' % run_name]

    ###
    header += stats_pairwise
    if len(condition_names) > 2:
        header += stats_one_vs_all
    ###

    return header
def get_ordered_condition_run_names(cond2run_dict):
    condition_names = sorted(list(set(cond2run_dict.keys())))
    run_names = sorted(list(set(sum(list(cond2run_dict.values()), []))))
    return condition_names,run_names

def generate_result_table(models, cond2run_dict):  # per gene/transcript
    """
    Generate a table containing learned model parameters and statistic tests. methods['pooling'] = False

    Parameters
    ----------
    models
        Learned models for individual genomic positions of a gene.
    group_labels
        Labels of samples.
    cond2run_dict
        Dict mapping condition_names to list of run_names

    Returns
    -------
    table
        List of tuples.
    """

    ###
    condition_names,run_names = get_ordered_condition_run_names(cond2run_dict)
    ###

    ###
    table = []
    for key, model in models.items():
        idx, position, kmer = key
        mu = model.nodes['mu_tau'].expected()  # K
        sigma2 = 1./model.nodes['mu_tau'].expected(var='gamma')  # K
        var_mu = model.nodes['mu_tau'].variance(var='normal')  # K
        # mu = model.nodes['y'].params['mean']
        # sigma2 = model.nodes['y'].params['variance']
        w = model.nodes['w'].expected()  # GK
        N = model.nodes['y'].params['N'].round()  # GK
        N0 = N[:, 0].squeeze()
        N1 = N[:, 1].squeeze()
        w0 = w[:, 0].squeeze()
        coverage = numpy.sum(model.nodes['y'].params['N'], axis=-1)  # GK => G # n_reads per group

        p_overlap, list_cdf_at_intersections = stats.calc_prob_overlapping(mu, sigma2)

        model_group_names = model.nodes['x'].params['group_names']

        ### calculate stats_pairwise
        stats_pairwise = []
        for cond1, cond2 in itertools.combinations(condition_names, 2):
            runs1, runs2 = cond2run_dict[cond1], cond2run_dict[cond2]
            w_cond1 = w[numpy.isin(model_group_names, runs1), 0].flatten()
            w_cond2 = w[numpy.isin(model_group_names, runs2), 0].flatten()
            n_cond1 = coverage[numpy.isin(model_group_names, runs1)]
            n_cond2 = coverage[numpy.isin(model_group_names, runs2)]

            z_score, p_ws = stats.z_test(w_cond1, w_cond2, n_cond1, n_cond2)
            ws_mean_diff = abs(numpy.mean(w_cond1)-numpy.mean(w_cond2))
            abs_z_score = abs(z_score)

            stats_pairwise += [p_ws, ws_mean_diff, abs_z_score]

        if len(condition_names) > 2:
            ### calculate stats_one_vs_all
            stats_one_vs_all = []
            for condition_name in condition_names:
                runs1 = cond2run_dict[condition_name]
                w_cond1 = w[numpy.isin(model_group_names, runs1), 0].flatten()
                w_cond2 = w[~numpy.isin(model_group_names, runs1), 0].flatten()
                n_cond1 = coverage[numpy.isin(model_group_names, runs1)]
                n_cond2 = coverage[~numpy.isin(model_group_names, runs1)]

                z_score, p_ws = stats.z_test(w_cond1, w_cond2, n_cond1, n_cond2)
                ws_mean_diff = abs(numpy.mean(w_cond1)-numpy.mean(w_cond2))
                abs_z_score = abs(z_score)

                stats_one_vs_all += [p_ws, ws_mean_diff, abs_z_score]

        ### lower, higher clusters
        w_min = w0
        if mu[1] < mu[0]:
            mu = mu[::-1]
            sigma2 = sigma2[::-1]
            w_min = 1-w0
        ###
        w_min_ordered, coverage_ordered = [], []
        for run_name in run_names:
            w_min_ordered += list(w_min[numpy.isin(model_group_names, run_name)])
            coverage_ordered += list(coverage[numpy.isin(model_group_names, run_name)])
        ###
        ### prepare values to write
        row = [idx, position, kmer] + list(mu) + list(sigma2) + [p_overlap]
        row += list_cdf_at_intersections
        row += list(w_min_ordered) + list(coverage_ordered)

        row += stats_pairwise
        if len(condition_names) > 2:
            row += stats_one_vs_all

        table += [tuple(row)]

    return table