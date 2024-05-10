from model.classical_methods.base import classical_methods
from copy import deepcopy
import os.path as ops
import pickle

class LogRegMethod(classical_methods):
    def __init__(self, args, is_regression):
        super().__init__(args, is_regression)
        assert not is_regression
        assert(args.cat_policy != 'indices')

    def construct_model(self, model_config = None):
        if model_config is None:
            model_config = self.args.config['model']
        from sklearn.linear_model import LogisticRegression
        self.model = LogisticRegression(**model_config,random_state=self.args.seed)
    
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
        test_logit = self.model.predict_proba(self.N_test)
        vres, metric_name = self.metric(test_logit, test_label, self.y_info)
        return vres, metric_name, test_logit