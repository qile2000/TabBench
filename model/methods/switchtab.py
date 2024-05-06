from model.methods.base import Method
import time
import torch
import os.path as osp
import numpy as np
import torch.nn.functional as F
from model.utils import (
    Averager
)
from model.lib.data import (
    Dataset,
    data_nan_process,
    data_enc_process,
    data_norm_process,
    data_label_process,
    data_loader_process,
    get_categories
)


class SwitchTabMethod(Method):
    def __init__(self, args, is_regression):
        super().__init__(args, is_regression)
        assert(args.cat_policy != 'indices')


    def construct_model(self, model_config = None):
        from model.models.switchtab import SwitchTab
        if model_config is None:
            model_config = self.args.config['model']
        self.model = SwitchTab(
            feature_size=self.d_in,
            num_classes=self.d_out,
            **model_config  # num_heads=2, alpha=1.0
        ).to(self.args.device)
        self.model.double()


    # Feature corruption + feature num must be even
    def data_format(self, is_train = True, N = None, C = None, y = None):
        if is_train:
            from model.models.switchtab import feature_corruption
            self.N, self.C, self.num_new_value, self.imputer, self.cat_new_value = data_nan_process(self.N, self.C, self.args.num_nan_policy, self.args.cat_nan_policy)
            self.y, self.y_info, self.label_encoder = data_label_process(self.y, self.is_regression)
            self.N, self.C, self.ord_encoder, self.mode_values, self.cat_encoder = data_enc_process(self.N, self.C, self.args.cat_policy, self.y['train'])
            self.N, self.normalizer = data_norm_process(self.N, self.args.normalization, self.args.seed)

            
            if self.is_regression:
                self.d_out = 1
            else:
                self.d_out = len(np.unique(self.y['train']))
            self.d_in = 0 if self.N is None else self.N['train'].shape[1]
            self.categories = get_categories(self.C)
            self.N['train'] = feature_corruption(torch.from_numpy(self.N['train'])).numpy()

            self.N, self.C, self.y, self.train_loader, self.val_loader, self.criterion = data_loader_process(self.is_regression, (self.N, self.C), self.y, self.y_info, self.args.device, self.args.batch_size, is_train = True)
            self.recon_criterion = F.mse_loss
        else:
            N_test, C_test, _, _, _ = data_nan_process(N, C, self.args.num_nan_policy, self.args.cat_nan_policy, self.num_new_value, self.imputer, self.cat_new_value)
            y_test, _, _ = data_label_process(y, self.is_regression, self.y_info, self.label_encoder)
            N_test, C_test, _, _, _ = data_enc_process(N_test, C_test, self.args.cat_policy, None, self.ord_encoder, self.mode_values, self.cat_encoder)
            N_test, _ = data_norm_process(N_test, self.args.normalization, self.args.seed, self.normalizer)
            _, _, _, self.test_loader, _ =  data_loader_process(self.is_regression, (N_test, C_test), y_test, self.y_info, self.args.device, self.args.batch_size, is_train = False)


    def fit(self, N, C, y, info, train = True, config = None):
        # if the method already fit the dataset, skip these steps (such as the hyper-tune process)
        if self.D is None:
            self.D = Dataset(N, C, y, info)
            self.N, self.C, self.y = self.D.N, self.D.C, self.D.y
            self.is_binclass, self.is_multiclass, self.is_regression = self.D.is_binclass, self.D.is_multiclass, self.D.is_regression
            self.n_num_features, self.n_cat_features = self.D.n_num_features, self.D.n_cat_features
            
            self.data_format(is_train = True)
        if config is not None:
            self.reset_stats_withconfig(config)
        self.construct_model()
        self.optimizer = torch.optim.RMSprop(
            self.model.parameters(), 
            lr=self.args.config['training']['lr'], 
            weight_decay=self.args.config['training']['weight_decay']
        )
        # if not train, skip the training process. such as load the checkpoint and directly predict the results
        if not train:
            return
        
        for epoch in range(self.args.max_epoch):
            tic = time.time()
            self.train_epoch(epoch)
            self.validate(epoch)
            elapsed = time.time() - tic
            print(f'Epoch: {epoch}, Time cost: {elapsed}')
            if not self.continue_training:
                break
        torch.save(
            dict(params=self.model.state_dict()),
            osp.join(self.args.save_path, 'epoch-last-{}.pth'.format(str(self.args.seed)))
        )

    def train_epoch(self, epoch):
        self.model.train()
        tl = Averager()
        for i, ((X1, y1), (X2, y2)) in enumerate(zip(self.train_loader, self.train_loader), 1):
            self.train_step = self.train_step + 1
            if self.N is not None and self.C is not None:
                X1_num, X1_cat = X1[0], X1[1]
                X2_num, X2_cat = X2[0], X2[1]
            elif self.C is not None and self.N is None:
                X1_num, X1_cat = None, X1
                X2_num, X2_cat = None, X2
            else:
                X1_num, X1_cat = X1, None
                X2_num, X2_cat = X2, None
                
            # categorical features are encoded to X_num
            assert X1_num is not None and X1_cat is None
            assert X2_num is not None and X2_cat is None
            
            X1_recon, X2_recon, X1_switched, X2_switched, X1_pred, X2_pred, alpha = self.model(X1_num, X2_num)

            recon_loss = self.recon_criterion(X1_recon, X1_num) + self.recon_criterion(X1_switched, X1_num) + self.recon_criterion(X2_recon, X2_num) + self.recon_criterion(X2_switched, X2_num)
            sup_loss = self.criterion(X1_pred, y1) + self.criterion(X2_pred, y2)
            
            loss = recon_loss + alpha * sup_loss
            
            tl.add(loss.item())
            self.optimizer.zero_grad()
            loss.backward()
            self.optimizer.step()
            
            if (i-1) % 50 == 0 or i == len(self.train_loader):
                print('epoch {}, train {}/{}, loss={:.4f} lr={:.4g}'.format(
                    epoch, i, len(self.train_loader), loss.item(), self.optimizer.param_groups[0]['lr']))
            del loss, recon_loss, sup_loss
        tl = tl.item()
        self.trlog['train_loss'].append(tl)    