from model.classical_methods.base import classical_methods
from copy import deepcopy
import os.path as ops
import pickle

class SvmMethod(classical_methods):
    def __init__(self, args, is_regression):
        super().__init__(args, is_regression)
        assert(args.cat_policy != 'indices')

    def construct_model(self, model_config = None):
        if model_config is None:
            model_config = self.args.config['model']
        from sklearn.svm import SVC, SVR
        if self.is_regression:
            self.model = SVR(**model_config)
        else:
            self.model = SVC(**model_config,random_state=self.args.seed)


    def fit(self, N, C, y, info, train=True, config=None):
        super().fit(N, C, y, info, train, config)
        # if not train, skip the training process. such as load the checkpoint and directly predict the results
        if not train:
            return
        self.model.fit(self.N['train'], self.y['train'])
        self.trlog['best_res'] = self.model.score(self.N['val'], self.y['val'])
        with open(ops.join(self.args.save_path , 'best-val-{}.pkl'.format(self.args.seed)), 'wb') as f:
            pickle.dump(self.model, f)

    def predict(self, N, C, y, info, model_name):
        with open(ops.join(self.args.save_path , 'best-val-{}.pkl'.format(self.args.seed)), 'rb') as f:
            self.model = pickle.load(f)
        self.data_format(False, N, C, y)
        test_label = self.y_test
        test_logit = self.model.predict(self.N_test)
        vres, metric_name = self.metric(test_logit, test_label, self.y_info)
        return vres, metric_name, test_logit
    
    def metric(self, predictions, labels, y_info):
        from sklearn import metrics as skm
        if self.is_regression:
            mae = skm.mean_absolute_error(labels, predictions)
            rmse = skm.mean_squared_error(labels, predictions) ** 0.5
            r2 = skm.r2_score(labels, predictions)
            if y_info['policy'] == 'mean_std':
                mae *= y_info['std']
                rmse *= y_info['std']
            return (mae,r2,rmse), ("MAE", "R2", "RMSE")
        else:
            accuracy = skm.accuracy_score(labels, predictions)
            avg_precision = skm.precision_score(labels, predictions, average='macro')
            avg_recall = skm.recall_score(labels, predictions, average='macro')
            f1_score = skm.f1_score(labels, predictions, average='binary' if self.is_binclass else 'macro')
            return (accuracy, avg_precision, avg_recall, f1_score), ("Accuracy", "Avg_Precision", "Avg_Recall", "F1")