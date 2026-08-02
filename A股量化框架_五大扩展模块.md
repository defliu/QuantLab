# A股量化框架 — 五大扩展模块（完整版）

---

## 模块一：机器学习因子挖掘（LightGBM / XGBoost）

### 核心思路

```
传统打分法:  因子值 → 线性加权 → 综合得分 → 选股
ML方法:      因子值 → LightGBM非线性拟合 → 预测下期收益 → 排序选股
```

### 完整代码

```python
import lightgbm as lgb
import xgboost as xgb
from sklearn.model_selection import TimeSeriesSplit
from sklearn.metrics import mean_squared_error
import pandas as pd
import numpy as np
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass
import warnings
warnings.filterwarnings("ignore")


# ============================================================
#  1. 样本构建（关键：避免未来函数）
# ============================================================

@dataclass
class MLSampleConfig:
    """机器学习样本配置"""
    forward_period: int = 5          # 预测未来N日收益
    train_window: int = 504          # 训练窗口（约2年）
    valid_window: int = 63           # 验证窗口（约3个月）
    retrain_freq: int = 21           # 重训练频率（每月）
    min_samples: int = 1000          # 最少样本数
    label_type: str = "return"       # "return" 收益率 / "rank" 排序分位


class SampleBuilder:
    """
    构建ML训练样本
    
    核心原则：
    - 特征用T日数据
    - 标签用T+forward_period日收益
    - 训练集严格在验证集之前（时间序列切分）
    """
    
    def __init__(self, config: MLSampleConfig):
        self.config = config
    
    def build_dataset(self, factor_panel: pd.DataFrame,
                      price_data: pd.DataFrame) -> Tuple[pd.DataFrame, pd.Series]:
        """
        构建完整数据集
        
        factor_panel: MultiIndex (date, code) → 各因子列
        price_data: MultiIndex (date, code) → close列
        
        返回: (特征矩阵X, 标签y)
        """
        # 计算前向收益（标签）
        returns = price_data['close'].groupby(level='code').pct_change(
            self.config.forward_period
        ).shift(-self.config.forward_period)
        
        # 合并特征和标签
        dataset = factor_panel.copy()
        dataset['forward_return'] = returns
        
        # 去除NaN
        dataset = dataset.dropna()
        
        X = dataset.drop(columns=['forward_return'])
        y = dataset['forward_return']
        
        if self.config.label_type == "rank":
            # 转换为截面排序分位数（更稳健）
            y = y.groupby(level='date').rank(pct=True)
        
        return X, y
    
    def time_series_split(self, X: pd.DataFrame, y: pd.Series,
                          dates: pd.DatetimeIndex) -> List[Tuple]:
        """
        时间序列滚动切分（Walk-Forward）
        
        避免传统K-Fold在时间序列上的数据泄露
        """
        splits = []
        unique_dates = sorted(dates.unique())
        
        start = 0
        while start + self.config.train_window + self.config.valid_window < len(unique_dates):
            train_end = start + self.config.train_window
            valid_end = train_end + self.config.valid_window
            
            train_dates = unique_dates[start:train_end]
            valid_dates = unique_dates[train_end:valid_end]
            
            train_mask = dates.isin(train_dates)
            valid_mask = dates.isin(valid_dates)
            
            splits.append((
                np.where(train_mask)[0],
                np.where(valid_mask)[0]
            ))
            
            start += self.config.retrain_freq  # 滚动步长
        
        return splits


# ============================================================
#  2. 特征工程（因子衍生 + 交互特征）
# ============================================================

class FeatureEngineer:
    """特征工程：从基础因子衍生更多特征"""
    
    @staticmethod
    def add_cross_features(factor_df: pd.DataFrame) -> pd.DataFrame:
        """添加因子交互特征"""
        result = factor_df.copy()
        cols = factor_df.columns.tolist()
        
        # 两两交互（取前5个重要因子，避免维度爆炸）
        for i in range(min(5, len(cols))):
            for j in range(i + 1, min(5, len(cols))):
                result[f"{cols[i]}_x_{cols[j]}"] = (
                    factor_df[cols[i]] * factor_df[cols[j]]
                )
        
        return result
    
    @staticmethod
    def add_time_features(factor_df: pd.DataFrame, 
                          dates: pd.DatetimeIndex) -> pd.DataFrame:
        """添加时间特征（捕捉日历效应）"""
        result = factor_df.copy()
        result['month'] = dates.month
        result['day_of_week'] = dates.dayofweek
        result['is_month_end'] = (dates.day > 25).astype(int)
        result['is_quarter_end'] = dates.is_quarter_end.astype(int)
        return result
    
    @staticmethod
    def add_market_features(factor_df: pd.DataFrame,
                            market_data: pd.DataFrame) -> pd.DataFrame:
        """添加市场环境特征"""
        result = factor_df.copy()
        
        # 市场波动率（20日）
        mkt_vol = market_data['close'].pct_change().rolling(20).std()
        result['market_volatility'] = mkt_vol.reindex(result.index.get_level_values('date')).values
        
        # 市场动量
        mkt_mom = market_data['close'].pct_change(20)
        result['market_momentum'] = mkt_mom.reindex(result.index.get_level_values('date')).values
        
        # 市场成交额变化
        vol_change = market_data['amount'].pct_change(5)
        result['volume_change'] = vol_change.reindex(result.index.get_level_values('date')).values
        
        return result
    
    @staticmethod
    def add_factor_momentum(factor_df: pd.DataFrame, 
                            window: int = 20) -> pd.DataFrame:
        """因子动量：因子值的变化趋势"""
        result = factor_df.copy()
        for col in factor_df.columns:
            result[f"{col}_delta"] = factor_df[col].groupby(
                level='code'
            ).diff(window)
        return result


# ============================================================
#  3. LightGBM 因子合成模型
# ============================================================

class LightGBMFactorModel:
    """
    基于LightGBM的非线性因子合成
    
    支持两种模式:
    - regression: 预测收益率（MSE损失）
    - lambdarank: 排序学习（LambdaRank损失，更适合选股）
    """
    
    def __init__(self, mode: str = "regression",
                 params: Optional[Dict] = None):
        self.mode = mode
        self.model = None
        self.feature_importance = None
        
        if params is None:
            if mode == "regression":
                self.params = {
                    'objective': 'regression',
                    'metric': 'mse',
                    'boosting_type': 'gbdt',
                    'num_leaves': 63,
                    'max_depth': 7,
                    'learning_rate': 0.05,
                    'feature_fraction': 0.8,      # 特征采样
                    'bagging_fraction': 0.8,      # 样本采样
                    'bagging_freq': 5,
                    'min_child_samples': 50,
                    'lambda_l1': 0.1,             # L1正则
                    'lambda_l2': 1.0,             # L2正则
                    'verbose': -1,
                    'n_jobs': -1,
                    'seed': 42,
                }
            elif mode == "lambdarank":
                self.params = {
                    'objective': 'lambdarank',
                    'metric': 'ndcg',
                    'ndcg_eval_at': [10, 30, 50],  # 关注Top N
                    'boosting_type': 'gbdt',
                    'num_leaves': 63,
                    'max_depth': 7,
                    'learning_rate': 0.05,
                    'feature_fraction': 0.8,
                    'bagging_fraction': 0.8,
                    'bagging_freq': 5,
                    'min_child_samples': 50,
                    'lambda_l1': 0.1,
                    'lambda_l2': 1.0,
                    'verbose': -1,
                    'n_jobs': -1,
                    'seed': 42,
                }
        else:
            self.params = params
    
    def train(self, X_train: pd.DataFrame, y_train: pd.Series,
              X_valid: pd.DataFrame, y_valid: pd.Series,
              num_boost_round: int = 500,
              early_stopping_rounds: int = 50) -> Dict:
        """
        训练模型
        
        对于lambdarank模式，需要按日期分组
        """
        train_data = lgb.Dataset(X_train, label=y_train)
        valid_data = lgb.Dataset(X_valid, label=y_valid, reference=train_data)
        
        if self.mode == "lambdarank":
            # 计算每个日期的样本数（group）
            train_groups = X_train.index.get_level_values('date').value_counts().sort_index().values
            valid_groups = X_valid.index.get_level_values('date').value_counts().sort_index().values
            train_data.set_group(train_groups)
            valid_data.set_group(valid_groups)
        
        callbacks = [
            lgb.early_stopping(early_stopping_rounds),
            lgb.log_evaluation(period=100)
        ]
        
        self.model = lgb.train(
            self.params,
            train_data,
            num_boost_round=num_boost_round,
            valid_sets=[train_data, valid_data],
            valid_names=['train', 'valid'],
            callbacks=callbacks
        )
        
        # 特征重要性
        self.feature_importance = pd.Series(
            self.model.feature_importance(importance_type='gain'),
            index=X_train.columns
        ).sort_values(ascending=False)
        
        # 评估
        train_pred = self.model.predict(X_train)
        valid_pred = self.model.predict(X_valid)
        
        metrics = {
            'train_mse': mean_squared_error(y_train, train_pred),
            'valid_mse': mean_squared_error(y_valid, valid_pred),
            'best_iteration': self.model.best_iteration,
            'top_features': self.feature_importance.head(10).to_dict()
        }
        
        return metrics
    
    def predict(self, X: pd.DataFrame) -> pd.Series:
        """预测"""
        if self.model is None:
            raise ValueError("模型未训练")
        pred = self.model.predict(X)
        return pd.Series(pred, index=X.index, name='ml_score')
    
    def get_feature_importance(self, top_n: int = 20) -> pd.Series:
        """获取特征重要性"""
        return self.feature_importance.head(top_n)


# ============================================================
#  4. XGBoost 备选模型
# ============================================================

class XGBoostFactorModel:
    """XGBoost因子合成（备选）"""
    
    def __init__(self, params: Optional[Dict] = None):
        self.model = None
        self.params = params or {
            'objective': 'reg:squarederror',
            'max_depth': 6,
            'learning_rate': 0.05,
            'subsample': 0.8,
            'colsample_bytree': 0.8,
            'min_child_weight': 50,
            'reg_alpha': 0.1,
            'reg_lambda': 1.0,
            'tree_method': 'hist',
            'seed': 42,
            'verbosity': 0,
        }
    
    def train(self, X_train: pd.DataFrame, y_train: pd.Series,
              X_valid: pd.DataFrame, y_valid: pd.Series,
              num_boost_round: int = 500,
              early_stopping_rounds: int = 50):
        
        dtrain = xgb.DMatrix(X_train, label=y_train)
        dvalid = xgb.DMatrix(X_valid, label=y_valid)
        
        self.model = xgb.train(
            self.params,
            dtrain,
            num_boost_round=num_boost_round,
            evals=[(dtrain, 'train'), (dvalid, 'valid')],
            early_stopping_rounds=early_stopping_rounds,
            verbose_eval=100
        )
    
    def predict(self, X: pd.DataFrame) -> pd.Series:
        dmatrix = xgb.DMatrix(X)
        pred = self.model.predict(dmatrix)
        return pd.Series(pred, index=X.index, name='ml_score')


# ============================================================
#  5. 滚动训练框架（Walk-Forward）
# ============================================================

class RollingMLTrainer:
    """
    滚动训练框架
    
    每月重新训练模型，避免过拟合 + 适应市场变化
    """
    
    def __init__(self, model_class, sample_config: MLSampleConfig,
                 feature_engineer: FeatureEngineer):
        self.model_class = model_class
        self.sample_config = sample_config
        self.feature_engineer = feature_engineer
        self.models: Dict[str, object] = {}  # date -> model
        self.predictions: List[pd.Series] = []
    
    def run(self, factor_panel: pd.DataFrame,
            price_data: pd.DataFrame,
            market_data: pd.DataFrame) -> pd.Series:
        """
        执行滚动训练 + 预测
        
        返回: 全样本预测得分
        """
        sample_builder = SampleBuilder(self.sample_config)
        X, y = sample_builder.build_dataset(factor_panel, price_data)
        
        # 特征工程
        X = self.feature_engineer.add_cross_features(X)
        dates = X.index.get_level_values('date')
        X = self.feature_engineer.add_time_features(X, dates)
        X = self.feature_engineer.add_market_features(X, market_data)
        
        # 时间序列切分
        splits = sample_builder.time_series_split(X, y, dates)
        
        all_predictions = []
        
        for i, (train_idx, valid_idx) in enumerate(splits):
            X_train, y_train = X.iloc[train_idx], y.iloc[train_idx]
            X_valid, y_valid = X.iloc[valid_idx], y.iloc[valid_idx]
            
            # 训练
            model = self.model_class()
            metrics = model.train(X_train, y_train, X_valid, y_valid)
            
            # 记录模型
            valid_start_date = dates.iloc[valid_idx[0]]
            self.models[str(valid_start_date.date())] = model
            
            # 在验证集上预测
            pred = model.predict(X_valid)
            all_predictions.append(pred)
            
            print(f"[Fold {i+1}/{len(splits)}] "
                  f"Valid MSE: {metrics['valid_mse']:.6f}, "
                  f"Best Iter: {metrics['best_iteration']}")
        
        # 合并所有预测
        full_pred = pd.concat(all_predictions)
        full_pred = full_pred[~full_pred.index.duplicated(keep='last')]
        
        return full_pred
    
    def get_stability_report(self) -> Dict:
        """模型稳定性报告"""
        importances = []
        for date, model in self.models.items():
            if hasattr(model, 'feature_importance') and model.feature_importance is not None:
                importances.append(model.feature_importance)
        
        if not importances:
            return {}
        
        imp_df = pd.DataFrame(importances)
        return {
            "特征重要性均值": imp_df.mean().sort_values(ascending=False).head(10),
            "特征重要性标准差": imp_df.std().sort_values(ascending=False).head(10),
            "稳定性比率": (imp_df.mean() / (imp_df.std() + 1e-8)).sort_values(ascending=False).head(10),
        }


# ============================================================
#  6. 集成策略（多模型融合）
# ============================================================

class EnsemblePredictor:
    """多模型集成预测"""
    
    def __init__(self):
        self.models: List[Tuple[str, object]] = []
    
    def add_model(self, name: str, model):
        self.models.append((name, model))
    
    def predict(self, X: pd.DataFrame, 
                weights: Optional[Dict[str, float]] = None) -> pd.Series:
        """加权集成预测"""
        if weights is None:
            weights = {name: 1.0 / len(self.models) for name, _ in self.models}
        
        total_weight = sum(weights.values())
        ensemble_pred = pd.Series(0.0, index=X.index)
        
        for name, model in self.models:
            pred = model.predict(X)
            # 标准化后加权
            pred_norm = (pred - pred.mean()) / (pred.std() + 1e-8)
            ensemble_pred += (weights.get(name, 0) / total_weight) * pred_norm
        
        return ensemble_pred


# ============================================================
#  使用示例
# ============================================================

def ml_factor_pipeline():
    """ML因子挖掘完整流水线"""
    
    # 1. 配置
    config = MLSampleConfig(
        forward_period=5,       # 预测未来5日收益
        train_window=504,       # 2年训练
        valid_window=63,        # 3个月验证
        retrain_freq=21,        # 每月重训
        label_type="return"
    )
    
    # 2. 特征工程
    fe = FeatureEngineer()
    
    # 3. 滚动训练
    trainer = RollingMLTrainer(
        model_class=LightGBMFactorModel,
        sample_config=config,
        feature_engineer=fe
    )
    
    # 假设已有数据
    # predictions = trainer.run(factor_panel, price_data, market_data)
    
    # 4. 稳定性报告
    # report = trainer.get_stability_report()
    
    # 5. 集成
    ensemble = EnsemblePredictor()
    # ensemble.add_model("lgb", lgb_model)
    # ensemble.add_model("xgb", xgb_model)
    # final_score = ensemble.predict(X_test)
    
    print("✅ ML因子挖掘流水线初始化完成")
```

---

## 模块二：组合优化（cvxpy）

### 核心思路

```
等权 → 得分加权 → 均值方差优化 → 风险平价 → 带约束的优化
                                    ↑ 我们在这里
```

### 完整代码

```python
import cvxpy as cp
import pandas as pd
import numpy as np
from typing import Dict, Optional, List
from dataclasses import dataclass
from enum import Enum


class OptMethod(Enum):
    MEAN_VARIANCE = "mean_variance"
    RISK_PARITY = "risk_parity"
    MIN_VARIANCE = "min_variance"
    MAX_SHARPE = "max_sharpe"
    BLACK_LITTERMAN = "black_litterman"
    RISK_BUDGET = "risk_budget"


@dataclass
class OptConstraints:
    """优化约束条件"""
    max_weight: float = 0.05           # 单股最大权重
    min_weight: float = 0.0            # 单股最小权重（0=允许空仓）
    max_industry_weight: float = 0.20  # 单行业最大权重
    max_turnover: float = 0.30         # 最大换手率
    target_tracking_error: float = None  # 目标跟踪误差（指数增强用）
    max_active_weight: float = 0.02    # 相对基准最大偏离


class PortfolioOptimizer:
    """
    基于cvxpy的组合优化器
    
    支持:
    - 均值-方差优化（Markowitz）
    - 最小方差组合
    - 最大夏普比率
    - 风险平价
    - 风险预算
    - 带行业/个股约束的优化
    """
    
    def __init__(self, constraints: OptConstraints = None):
        self.constraints = constraints or OptConstraints()
    
    # ----------------------------------------------------------
    #  均值-方差优化
    # ----------------------------------------------------------
    
    def mean_variance(self, expected_returns: np.ndarray,
                      cov_matrix: np.ndarray,
                      risk_aversion: float = 1.0,
                      benchmark_weights: np.ndarray = None) -> np.ndarray:
        """
        均值-方差优化
        
        max: μ'w - (γ/2) w'Σw
        s.t. sum(w) = 1, 0 ≤ w_i ≤ max_weight
        
        risk_aversion: 风险厌恶系数（越大越保守）
        """
        n = len(expected_returns)
        w = cp.Variable(n)
        
        # 目标函数: 最大化 收益 - 风险惩罚
        portfolio_return = expected_returns @ w
        portfolio_risk = cp.quad_form(w, cov_matrix)
        objective = cp.Maximize(portfolio_return - (risk_aversion / 2) * portfolio_risk)
        
        # 约束
        constraints = [
            cp.sum(w) == 1,
            w >= self.constraints.min_weight,
            w <= self.constraints.max_weight,
        ]
        
        # 跟踪误差约束（指数增强）
        if benchmark_weights is not None and self.constraints.target_tracking_error:
            active = w - benchmark_weights
            te = cp.quad_form(active, cov_matrix)
            constraints.append(te <= self.constraints.target_tracking_error ** 2)
        
        prob = cp.Problem(objective, constraints)
        prob.solve(solver=cp.OSQP, warm_start=True)
        
        if prob.status not in ['optimal', 'optimal_inaccurate']:
            print(f"⚠️ 优化未收敛: {prob.status}，回退等权")
            return np.ones(n) / n
        
        return np.array(w.value).flatten()
    
    # ----------------------------------------------------------
    #  最小方差组合
    # ----------------------------------------------------------
    
    def min_variance(self, cov_matrix: np.ndarray) -> np.ndarray:
        """
        最小方差组合
        
        min: w'Σw
        s.t. sum(w) = 1, w ≥ 0
        """
        n = cov_matrix.shape[0]
        w = cp.Variable(n)
        
        objective = cp.Minimize(cp.quad_form(w, cov_matrix))
        constraints = [
            cp.sum(w) == 1,
            w >= self.constraints.min_weight,
            w <= self.constraints.max_weight,
        ]
        
        prob = cp.Problem(objective, constraints)
        prob.solve(solver=cp.OSQP, warm_start=True)
        
        if prob.status not in ['optimal', 'optimal_inaccurate']:
            return np.ones(n) / n
        
        return np.array(w.value).flatten()
    
    # ----------------------------------------------------------
    #  最大夏普比率
    # ----------------------------------------------------------
    
    def max_sharpe(self, expected_returns: np.ndarray,
                   cov_matrix: np.ndarray,
                   risk_free_rate: float = 0.02 / 252) -> np.ndarray:
        """
        最大夏普比率（Charnes-Cooper变换）
        
        将分式规划转化为凸规划
        """
        n = len(expected_returns)
        w = cp.Variable(n)
        kappa = cp.Variable()  # 辅助变量
        
        excess_returns = expected_returns - risk_free_rate
        
        # Charnes-Cooper变换: 令 y = w/κ, 则 κ = 1/(σ'w)
        y = cp.Variable(n)
        
        objective = cp.Maximize(excess_returns @ y)
        constraints = [
            cp.quad_form(y, cov_matrix) <= 1,  # y'Σy ≤ 1
            cp.sum(y) == kappa,
            y >= 0,
            kappa >= 0,
        ]
        
        prob = cp.Problem(objective, constraints)
        prob.solve(solver=cp.SCS)
        
        if prob.status not in ['optimal', 'optimal_inaccurate']:
            return np.ones(n) / n
        
        # 还原权重
        weights = np.array(y.value).flatten() / kappa.value
        weights = np.maximum(weights, 0)
        weights /= weights.sum()
        
        return weights
    
    # ----------------------------------------------------------
    #  风险平价（Risk Parity）
    # ----------------------------------------------------------
    
    def risk_parity(self, cov_matrix: np.ndarray,
                    max_iter: int = 1000,
                    tol: float = 1e-8) -> np.ndarray:
        """
        风险平价组合
        
        目标: 每只股票对组合风险的贡献相等
        RC_i = w_i * (Σw)_i / (w'Σw) = 1/n
        
        使用迭代法求解
        """
        n = cov_matrix.shape[0]
        w = np.ones(n) / n  # 初始等权
        
        for _ in range(max_iter):
            sigma_w = cov_matrix @ w
            port_var = w @ sigma_w
            port_vol = np.sqrt(port_var)
            
            # 边际风险贡献
            mrc = sigma_w / port_vol
            # 风险贡献
            rc = w * mrc
            # 目标风险贡献
            target_rc = port_vol / n
            
            # 更新权重
            w_new = w * (target_rc / (rc + 1e-10))
            w_new = w_new / w_new.sum()  # 归一化
            
            if np.max(np.abs(w_new - w)) < tol:
                break
            w = w_new
        
        return w
    
    # ----------------------------------------------------------
    #  风险预算（Risk Budget）
    # ----------------------------------------------------------
    
    def risk_budget(self, cov_matrix: np.ndarray,
                    budgets: np.ndarray = None) -> np.ndarray:
        """
        风险预算组合
        
        允许自定义每只股票的风险贡献比例
        budgets: 风险预算向量（默认等分）
        """
        if budgets is None:
            n = cov_matrix.shape[0]
            budgets = np.ones(n) / n
        
        n = len(budgets)
        w = cp.Variable(n)
        
        # 使用对数变换将问题凸化
        # min: w'Σw - Σ b_i * log(w_i)
        objective = cp.Minimize(
            cp.quad_form(w, cov_matrix) - budgets @ cp.log(w)
        )
        constraints = [
            w >= 1e-6,  # 严格正
            cp.sum(w) == 1,
        ]
        
        prob = cp.Problem(objective, constraints)
        prob.solve(solver=cp.SCS)
        
        if prob.status not in ['optimal', 'optimal_inaccurate']:
            return np.ones(n) / n
        
        weights = np.array(w.value).flatten()
        weights = np.maximum(weights, 0)
        weights /= weights.sum()
        return weights
    
    # ----------------------------------------------------------
    #  带行业约束的优化
    # ----------------------------------------------------------
    
    def optimize_with_industry(self, expected_returns: np.ndarray,
                               cov_matrix: np.ndarray,
                               industry_labels: np.ndarray,
                               risk_aversion: float = 1.0) -> np.ndarray:
        """
        带行业约束的均值-方差优化
        """
        n = len(expected_returns)
        w = cp.Variable(n)
        
        portfolio_return = expected_returns @ w
        portfolio_risk = cp.quad_form(w, cov_matrix)
        objective = cp.Maximize(portfolio_return - (risk_aversion / 2) * portfolio_risk)
        
        constraints = [
            cp.sum(w) == 1,
            w >= self.constraints.min_weight,
            w <= self.constraints.max_weight,
        ]
        
        # 行业约束
        unique_industries = np.unique(industry_labels)
        for ind in unique_industries:
            mask = (industry_labels == ind).astype(float)
            constraints.append(mask @ w <= self.constraints.max_industry_weight)
        
        prob = cp.Problem(objective, constraints)
        prob.solve(solver=cp.OSQP, warm_start=True)
        
        if prob.status not in ['optimal', 'optimal_inaccurate']:
            return np.ones(n) / n
        
        return np.array(w.value).flatten()
    
    # ----------------------------------------------------------
    #  换手率约束
    # ----------------------------------------------------------
    
    def optimize_with_turnover(self, expected_returns: np.ndarray,
                               cov_matrix: np.ndarray,
                               current_weights: np.ndarray,
                               risk_aversion: float = 1.0) -> np.ndarray:
        """
        带换手率约束的优化（减少交易成本）
        """
        n = len(expected_returns)
        w = cp.Variable(n)
        
        portfolio_return = expected_returns @ w
        portfolio_risk = cp.quad_form(w, cov_matrix)
        
        # 换手率惩罚
        turnover = cp.norm1(w - current_weights)
        transaction_cost = 0.003 * turnover  # 假设单边交易成本0.3%
        
        objective = cp.Maximize(
            portfolio_return - (risk_aversion / 2) * portfolio_risk - transaction_cost
        )
        
        constraints = [
            cp.sum(w) == 1,
            w >= self.constraints.min_weight,
            w <= self.constraints.max_weight,
            turnover <= self.constraints.max_turnover,
        ]
        
        prob = cp.Problem(objective, constraints)
        prob.solve(solver=cp.OSQP, warm_start=True)
        
        if prob.status not in ['optimal', 'optimal_inaccurate']:
            return current_weights
        
        return np.array(w.value).flatten()


# ============================================================
#  协方差矩阵估计（改进版）
# ============================================================

class CovarianceEstimator:
    """协方差矩阵估计器"""
    
    @staticmethod
    def sample_cov(returns: pd.DataFrame) -> np.ndarray:
        """样本协方差"""
        return returns.cov().values
    
    @staticmethod
    def shrinkage_cov(returns: pd.DataFrame, 
                      shrinkage: float = 0.3) -> np.ndarray:
        """
        Ledoit-Wolf收缩估计
        
        Σ_shrink = (1-δ)Σ_sample + δ * μ * I
        μ = trace(Σ_sample) / n
        """
        cov_sample = returns.cov().values
        n = cov_sample.shape[0]
        mu = np.trace(cov_sample) / n
        target = mu * np.eye(n)
        return (1 - shrinkage) * cov_sample + shrinkage * target
    
    @staticmethod
    def factor_model_cov(returns: pd.DataFrame,
                         factor_returns: pd.DataFrame) -> np.ndarray:
        """
        因子模型协方差（降维）
        
        Σ = B'F B + D
        B: 因子载荷矩阵
        F: 因子协方差
        D: 残差对角矩阵
        """
        from sklearn.linear_model import LinearRegression
        
        n_assets = returns.shape[1]
        n_factors = factor_returns.shape[1]
        
        # 估计因子载荷
        B = np.zeros((n_assets, n_factors))
        residuals = np.zeros((returns.shape[0], n_assets))
        
        for i in range(n_assets):
            reg = LinearRegression().fit(factor_returns, returns.iloc[:, i])
            B[i] = reg.coef_
            residuals[:, i] = reg.resid_
        
        # 因子协方差
        F = factor_returns.cov().values
        # 残差方差（对角）
        D = np.diag(residuals.var(axis=0))
        
        return B @ F @ B.T + D


# ============================================================
#  使用示例
# ============================================================

def portfolio_optimization_example():
    """组合优化使用示例"""
    
    # 模拟数据
    np.random.seed(42)
    n_stocks = 30
    expected_returns = np.random.randn(n_stocks) * 0.001 + 0.0005
    cov_matrix = np.random.randn(n_stocks, n_stocks) * 0.01
    cov_matrix = cov_matrix @ cov_matrix.T + np.eye(n_stocks) * 0.01
    
    # 约束
    constraints = OptConstraints(
        max_weight=0.05,
        max_industry_weight=0.20,
        max_turnover=0.30
    )
    
    optimizer = PortfolioOptimizer(constraints)
    
    # 方法1: 均值-方差
    w_mv = optimizer.mean_variance(expected_returns, cov_matrix, risk_aversion=2.0)
    print(f"均值-方差: 最大权重={w_mv.max():.3f}, 最小权重={w_mv.min():.3f}")
    
    # 方法2: 最小方差
    w_minvar = optimizer.min_variance(cov_matrix)
    print(f"最小方差: 组合波动率={np.sqrt(w_minvar @ cov_matrix @ w_minvar):.4f}")
    
    # 方法3: 风险平价
    w_rp = optimizer.risk_parity(cov_matrix)
    # 验证风险贡献是否相等
    sigma_w = cov_matrix @ w_rp
    rc = w_rp * sigma_w / np.sqrt(w_rp @ sigma_w)
    print(f"风险平价: 风险贡献标准差={rc.std():.6f} (越小越好)")
    
    # 方法4: 带换手率约束
    current_w = np.ones(n_stocks) / n_stocks
    w_turnover = optimizer.optimize_with_turnover(
        expected_returns, cov_matrix, current_w
    )
    turnover = np.abs(w_turnover - current_w).sum() / 2
    print(f"换手率约束: 实际换手率={turnover:.3f}")
    
    print("✅ 组合优化完成")
```

---

## 模块三：实盘对接（miniQMT / PTrade）

### 架构设计

```
┌──────────────────────────────────────────────────────┐
│                    策略引擎（回测/实盘共用）            │
├──────────────────────────────────────────────────────┤
│              Broker 抽象层（统一接口）                  │
├────────────┬────────────────┬────────────────────────┤
│  miniQMT   │    PTrade      │   模拟盘（回测）        │
│  (xtquant) │  (恒生)        │   (PaperTrading)       │
└────────────┴────────────────┴────────────────────────┘
```

### 完整代码

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Dict, List, Optional
from datetime import datetime, time
import pandas as pd
import numpy as np
import logging
import time as time_module

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger("Broker")


# ============================================================
#  1. Broker 抽象基类
# ============================================================

@dataclass
class Order:
    """委托单"""
    order_id: str
    code: str
    direction: str       # "buy" / "sell"
    price: float
    volume: int          # 股数（必须为100的整数倍）
    order_type: str = "limit"  # "limit" / "market"
    status: str = "pending"    # pending / filled / cancelled / rejected
    filled_price: float = 0.0
    filled_volume: int = 0
    create_time: str = ""
    update_time: str = ""


@dataclass
class AccountInfo:
    """账户信息"""
    total_asset: float       # 总资产
    available_cash: float    # 可用资金
    market_value: float      # 持仓市值
    frozen_cash: float       # 冻结资金


@dataclass
class PositionInfo:
    """持仓信息"""
    code: str
    name: str
    volume: int              # 持仓数量
    available_volume: int    # 可卖数量（T+1）
    cost_price: float        # 成本价
    current_price: float     # 现价
    market_value: float      # 市值
    profit: float            # 盈亏
    profit_pct: float        # 盈亏比例


class Broker(ABC):
    """券商接口抽象基类"""
    
    @abstractmethod
    def connect(self) -> bool:
        """连接"""
        pass
    
    @abstractmethod
    def disconnect(self):
        """断开"""
        pass
    
    @abstractmethod
    def get_account(self) -> AccountInfo:
        """查询账户"""
        pass
    
    @abstractmethod
    def get_positions(self) -> List[PositionInfo]:
        """查询持仓"""
        pass
    
    @abstractmethod
    def get_orders(self) -> List[Order]:
        """查询委托"""
        pass
    
    @abstractmethod
    def place_order(self, code: str, direction: str, 
                    price: float, volume: int) -> Optional[Order]:
        """下单"""
        pass
    
    @abstractmethod
    def cancel_order(self, order_id: str) -> bool:
        """撤单"""
        pass
    
    @abstractmethod
    def get_realtime_price(self, code: str) -> float:
        """获取实时价格"""
        pass
    
    @abstractmethod
    def subscribe_tick(self, codes: List[str], callback):
        """订阅Tick行情"""
        pass


# ============================================================
#  2. miniQMT 实现（xtquant）
# ============================================================

class MiniQMTBroker(Broker):
    """
    miniQMT实盘接口
    
    前置条件:
    1. 安装miniQMT客户端并登录
    2. pip install xtquant（或从miniQMT安装目录复制）
    3. 券商已开通miniQMT权限（2026年门槛约10-50万）
    """
    
    def __init__(self, path: str = r"D:\miniQMT\userdata_mini",
                 session_id: int = 123456):
        """
        path: miniQMT的userdata_mini路径
        session_id: 会话ID（自定义）
        """
        self.path = path
        self.session_id = session_id
        self.xt_trader = None
        self.xt_data = None
        self.account_id = ""
        self._connected = False
    
    def connect(self) -> bool:
        """连接miniQMT"""
        try:
            from xtquant import xttrader
            from xtquant import xtdata
            from xtquant.xttype import StockAccount
            
            # 创建交易对象
            self.xt_trader = xttrader.XtQuantTrader(self.path, self.session_id)
            self.xt_trader.start()
            
            # 建立连接
            connect_result = self.xt_trader.connect()
            if connect_result != 0:
                logger.error(f"❌ miniQMT连接失败，错误码: {connect_result}")
                return False
            
            # 创建账户对象（需替换为你的真实账号）
            self.account = StockAccount(self.account_id)
            
            # 订阅账户
            subscribe_result = self.xt_trader.subscribe(self.account)
            if subscribe_result != 0:
                logger.warning(f"⚠️ 账户订阅返回: {subscribe_result}")
            
            self.xt_data = xtdata
            self._connected = True
            logger.info("✅ miniQMT连接成功")
            return True
            
        except ImportError:
            logger.error("❌ 请先安装xtquant: pip install xtquant")
            return False
        except Exception as e:
            logger.error(f"❌ 连接异常: {e}")
            return False
    
    def disconnect(self):
        """断开连接"""
        if self.xt_trader:
            self.xt_trader.stop()
            self._connected = False
            logger.info("miniQMT已断开")
    
    def get_account(self) -> AccountInfo:
        """查询账户"""
        from xtquant.xttype import StockAccount
        
        asset = self.xt_trader.query_stock_asset(self.account)
        return AccountInfo(
            total_asset=asset.total_asset,
            available_cash=asset.cash,
            market_value=asset.market_value,
            frozen_cash=asset.frozen_cash
        )
    
    def get_positions(self) -> List[PositionInfo]:
        """查询持仓"""
        positions = self.xt_trader.query_stock_positions(self.account)
        result = []
        for pos in positions:
            if pos.volume > 0:
                result.append(PositionInfo(
                    code=pos.stock_code,
                    name="",
                    volume=pos.volume,
                    available_volume=pos.can_use_volume,
                    cost_price=pos.open_price,
                    current_price=pos.market_value / pos.volume if pos.volume > 0 else 0,
                    market_value=pos.market_value,
                    profit=pos.market_value - pos.open_price * pos.volume,
                    profit_pct=(pos.market_value / (pos.open_price * pos.volume) - 1) 
                              if pos.open_price > 0 else 0
                ))
        return result
    
    def get_orders(self) -> List[Order]:
        """查询当日委托"""
        orders = self.xt_trader.query_stock_orders(self.account)
        result = []
        for o in orders:
            result.append(Order(
                order_id=str(o.order_id),
                code=o.stock_code,
                direction="buy" if o.order_type == 23 else "sell",
                price=o.price,
                volume=o.order_volume,
                status=self._map_status(o.order_status),
                filled_price=o.traded_price,
                filled_volume=o.traded_volume,
            ))
        return result
    
    def place_order(self, code: str, direction: str,
                    price: float, volume: int) -> Optional[Order]:
        """
        下单
        
        code: 股票代码（如 "000001.SZ"）
        direction: "buy" / "sell"
        price: 委托价格
        volume: 委托数量（必须为100的整数倍）
        """
        from xtquant.xtconstant import (
            STOCK_BUY, STOCK_SELL, 
            FIX_PRICE, LATEST_PRICE
        )
        
        # 确保数量为100的整数倍
        volume = (volume // 100) * 100
        if volume <= 0:
            logger.warning(f"⚠️ 委托数量不足1手: {code}")
            return None
        
        # 方向
        order_type = STOCK_BUY if direction == "buy" else STOCK_SELL
        
        # 价格类型: FIX_PRICE=限价, LATEST_PRICE=最新价
        price_type = FIX_PRICE
        
        # 下单
        order_id = self.xt_trader.order_stock(
            self.account, code, order_type,
            volume, price_type, price,
            strategy_name="quant_strategy",
            order_remark=f"{direction}_{code}"
        )
        
        if order_id == -1:
            logger.error(f"❌ 下单失败: {code} {direction} {volume}股 @{price}")
            return None
        
        logger.info(f"📤 下单成功: {code} {direction} {volume}股 @{price}, 委托号={order_id}")
        
        return Order(
            order_id=str(order_id),
            code=code,
            direction=direction,
            price=price,
            volume=volume,
            status="pending"
        )
    
    def cancel_order(self, order_id: str) -> bool:
        """撤单"""
        result = self.xt_trader.cancel_order_stock(self.account, int(order_id))
        if result == 0:
            logger.info(f"✅ 撤单成功: {order_id}")
            return True
        logger.error(f"❌ 撤单失败: {order_id}")
        return False
    
    def get_realtime_price(self, code: str) -> float:
        """获取实时价格"""
        tick = self.xt_data.get_full_tick([code])
        if code in tick:
            return tick[code]['lastPrice']
        return 0.0
    
    def subscribe_tick(self, codes: List[str], callback):
        """订阅Tick行情"""
        for code in codes:
            self.xt_data.subscribe_quote(
                code, period='tick', callback=callback
            )
        logger.info(f"已订阅 {len(codes)} 只股票Tick行情")
    
    def download_history(self, codes: List[str], period: str = "1d",
                         start_time: str = "", end_time: str = ""):
        """下载历史数据"""
        self.xt_data.download_history_data2(
            codes, period=period,
            start_time=start_time, end_time=end_time
        )
    
    @staticmethod
    def _map_status(status_code: int) -> str:
        """状态码映射"""
        mapping = {
            48: "pending",      # 未报
            49: "pending",      # 待报
            50: "submitted",    # 已报
            51: "submitted",    # 已报待撤
            52: "partial",      # 部成
            53: "partial",      # 部撤
            54: "cancelled",    # 已撤
            55: "filled",       # 已成
            56: "rejected",     # 废单
        }
        return mapping.get(status_code, "unknown")


# ============================================================
#  3. PTrade 实现（恒生）
# ============================================================

class PTradeBroker(Broker):
    """
    PTrade实盘接口（恒生）
    
    注意: PTrade的API在券商终端内运行，代码结构略有不同
    这里提供适配层
    """
    
    def __init__(self):
        self._connected = False
        # PTrade在终端内运行，通过全局函数调用
        # 如: get_account(), order(), get_position() 等
    
    def connect(self) -> bool:
        """PTrade在终端内自动连接"""
        try:
            # PTrade环境检测
            account = get_account()  # PTrade内置函数
            self._connected = True
            logger.info("✅ PTrade连接成功")
            return True
        except NameError:
            logger.error("❌ 请在PTrade终端内运行")
            return False
    
    def disconnect(self):
        self._connected = False
    
    def get_account(self) -> AccountInfo:
        acc = get_account()
        return AccountInfo(
            total_asset=acc.total_asset,
            available_cash=acc.available_cash,
            market_value=acc.market_value,
            frozen_cash=acc.frozen_cash
        )
    
    def get_positions(self) -> List[PositionInfo]:
        positions = get_position()
        result = []
        for pos in positions:
            result.append(PositionInfo(
                code=pos.sid,
                name=pos.name,
                volume=pos.amount,
                available_volume=pos.available_amount,
                cost_price=pos.cost_basis,
                current_price=pos.last_sale_price,
                market_value=pos.market_value,
                profit=pos.profit,
                profit_pct=pos.profit_pct
            ))
        return result
    
    def get_orders(self) -> List[Order]:
        orders = get_orders()
        return [Order(
            order_id=str(o.id),
            code=o.sid,
            direction="buy" if o.is_buy else "sell",
            price=o.price,
            volume=o.amount,
            status=o.status
        ) for o in orders]
    
    def place_order(self, code: str, direction: str,
                    price: float, volume: int) -> Optional[Order]:
        volume = (volume // 100) * 100
        if volume <= 0:
            return None
        
        if direction == "buy":
            order_id = order(code, volume, style=LimitOrderStyle(price))
        else:
            order_id = order(code, -volume, style=LimitOrderStyle(price))
        
        if order_id:
            logger.info(f"📤 PTrade下单: {code} {direction} {volume}股 @{price}")
            return Order(order_id=str(order_id), code=code,
                        direction=direction, price=price, volume=volume)
        return None
    
    def cancel_order(self, order_id: str) -> bool:
        cancel_order(int(order_id))
        return True
    
    def get_realtime_price(self, code: str) -> float:
        data = get_price(code, frequency='tick', count=1)
        return data['close'].iloc[-1] if not data.empty else 0.0
    
    def subscribe_tick(self, codes: List[str], callback):
        # PTrade通过 handle_data 回调
        pass


# ============================================================
#  4. 模拟盘（Paper Trading）
# ============================================================

class PaperTradingBroker(Broker):
    """模拟盘（用于策略验证实盘逻辑）"""
    
    def __init__(self, initial_capital: float = 1_000_000):
        self.cash = initial_capital
        self.positions: Dict[str, PositionInfo] = {}
        self.orders: List[Order] = []
        self.order_counter = 0
        self._connected = False
    
    def connect(self) -> bool:
        self._connected = True
        logger.info("✅ 模拟盘已启动")
        return True
    
    def disconnect(self):
        self._connected = False
    
    def get_account(self) -> AccountInfo:
        market_value = sum(p.market_value for p in self.positions.values())
        return AccountInfo(
            total_asset=self.cash + market_value,
            available_cash=self.cash,
            market_value=market_value,
            frozen_cash=0
        )
    
    def get_positions(self) -> List[PositionInfo]:
        return list(self.positions.values())
    
    def get_orders(self) -> List[Order]:
        return self.orders
    
    def place_order(self, code: str, direction: str,
                    price: float, volume: int) -> Optional[Order]:
        volume = (volume // 100) * 100
        if volume <= 0:
            return None
        
        self.order_counter += 1
        order = Order(
            order_id=str(self.order_counter),
            code=code, direction=direction,
            price=price, volume=volume,
            status="filled",  # 模拟盘立即成交
            filled_price=price,
            filled_volume=volume,
            create_time=datetime.now().isoformat()
        )
        
        # 更新持仓和资金
        if direction == "buy":
            cost = price * volume * 1.00025  # 含佣金
            if cost > self.cash:
                order.status = "rejected"
                logger.warning(f"⚠️ 资金不足: 需要{cost:.0f}, 可用{self.cash:.0f}")
                return order
            self.cash -= cost
            
            if code in self.positions:
                pos = self.positions[code]
                total_vol = pos.volume + volume
                pos.cost_price = (pos.cost_price * pos.volume + price * volume) / total_vol
                pos.volume = total_vol
                pos.available_volume = pos.volume  # 简化
            else:
                self.positions[code] = PositionInfo(
                    code=code, name="", volume=volume,
                    available_volume=volume, cost_price=price,
                    current_price=price, market_value=price * volume,
                    profit=0, profit_pct=0
                )
        
        elif direction == "sell":
            if code not in self.positions:
                order.status = "rejected"
                return order
            pos = self.positions[code]
            if volume > pos.available_volume:
                volume = pos.available_volume
                order.volume = volume
            
            revenue = price * volume * (1 - 0.00025 - 0.001)  # 佣金+印花税
            self.cash += revenue
            pos.volume -= volume
            pos.available_volume -= volume
            if pos.volume <= 0:
                del self.positions[code]
        
        self.orders.append(order)
        logger.info(f"📝 模拟成交: {code} {direction} {volume}股 @{price}")
        return order
    
    def cancel_order(self, order_id: str) -> bool:
        return True
    
    def get_realtime_price(self, code: str) -> float:
        if code in self.positions:
            return self.positions[code].current_price
        return 0.0
    
    def subscribe_tick(self, codes: List[str], callback):
        pass
    
    def update_price(self, code: str, price: float):
        """更新持仓价格（外部调用）"""
        if code in self.positions:
            pos = self.positions[code]
            pos.current_price = price
            pos.market_value = price * pos.volume
            pos.profit = (price - pos.cost_price) * pos.volume
            pos.profit_pct = price / pos.cost_price - 1


# ============================================================
#  5. 实盘执行引擎
# ============================================================

class LiveTradingEngine:
    """
    实盘执行引擎
    
    职责:
    1. 接收策略信号
    2. 计算目标组合 vs 当前持仓的差异
    3. 生成交易指令
    4. 通过Broker执行
    5. 监控执行状态
    """
    
    def __init__(self, broker: Broker, 
                 commission_rate: float = 0.00025,
                 slippage: float = 0.002):
        self.broker = broker
        self.commission_rate = commission_rate
        self.slippage = slippage
    
    def execute_target_portfolio(self, target_weights: Dict[str, float],
                                 total_capital: float = None):
        """
        执行目标组合
        
        target_weights: {code: weight}
        """
        if not self.broker._connected:
            logger.error("❌ 未连接券商")
            return
        
        # 获取当前状态
        account = self.broker.get_account()
        if total_capital is None:
            total_capital = account.total_asset
        
        current_positions = {p.code: p for p in self.broker.get_positions()}
        
        # 计算目标持仓
        target_positions = {}
        for code, weight in target_weights.items():
            target_amount = total_capital * weight
            price = self.broker.get_realtime_price(code)
            if price > 0:
                target_shares = int(target_amount / price / 100) * 100
                target_positions[code] = target_shares
        
        # ===== 先卖后买 =====
        
        # 1. 卖出不在目标中的 / 需要减仓的
        for code, pos in current_positions.items():
            target_shares = target_positions.get(code, 0)
            if pos.available_volume > target_shares:
                sell_volume = pos.available_volume - target_shares
                sell_volume = (sell_volume // 100) * 100
                if sell_volume > 0:
                    price = self.broker.get_realtime_price(code)
                    sell_price = price * (1 - self.slippage)
                    self.broker.place_order(code, "sell", sell_price, sell_volume)
                    logger.info(f"🔴 卖出 {code}: {sell_volume}股 @{sell_price:.2f}")
        
        # 2. 买入新标的 / 需要加仓的
        for code, target_shares in target_positions.items():
            current_shares = current_positions.get(code, PositionInfo(
                code=code, name="", volume=0, available_volume=0,
                cost_price=0, current_price=0, market_value=0, profit=0, profit_pct=0
            )).volume
            
            if target_shares > current_shares:
                buy_volume = target_shares - current_shares
                buy_volume = (buy_volume // 100) * 100
                if buy_volume > 0:
                    price = self.broker.get_realtime_price(code)
                    buy_price = price * (1 + self.slippage)
                    
                    # 检查资金是否充足
                    cost = buy_volume * buy_price * (1 + self.commission_rate)
                    if cost <= account.available_cash:
                        self.broker.place_order(code, "buy", buy_price, buy_volume)
                        logger.info(f"🟢 买入 {code}: {buy_volume}股 @{buy_price:.2f}")
                    else:
                        logger.warning(f"⚠️ 资金不足，跳过 {code}")
        
        logger.info("✅ 组合调仓执行完毕")
    
    def check_and_execute_stop_loss(self, stop_loss_pct: float = 0.08):
        """检查并执行止损"""
        positions = self.broker.get_positions()
        for pos in positions:
            if pos.profit_pct <= -stop_loss_pct:
                if pos.available_volume > 0:
                    price = self.broker.get_realtime_price(pos.code)
                    sell_price = price * (1 - self.slippage)
                    self.broker.place_order(
                        pos.code, "sell", sell_price, pos.available_volume
                    )
                    logger.warning(f"🚨 止损卖出 {pos.code}: 亏损{pos.profit_pct:.2%}")
    
    def get_execution_report(self) -> Dict:
        """获取执行报告"""
        account = self.broker.get_account()
        positions = self.broker.get_positions()
        orders = self.broker.get_orders()
        
        return {
            "总资产": f"{account.total_asset:,.0f}",
            "可用资金": f"{account.available_cash:,.0f}",
            "持仓市值": f"{account.market_value:,.0f}",
            "持仓数量": len(positions),
            "当日委托数": len(orders),
            "持仓明细": [
                {
                    "代码": p.code,
                    "数量": p.volume,
                    "成本": f"{p.cost_price:.2f}",
                    "现价": f"{p.current_price:.2f}",
                    "盈亏": f"{p.profit_pct:.2%}"
                }
                for p in positions
            ]
        }


# ============================================================
#  6. 定时调度器
# ============================================================

import schedule

class TradingScheduler:
    """交易调度器"""
    
    def __init__(self, engine: LiveTradingEngine, strategy):
        self.engine = engine
        self.strategy = strategy
        self.is_trading_day = True
    
    def start(self):
        """启动调度"""
        # 盘前准备 (9:15)
        schedule.every().day.at("09:15").do(self.pre_market)
        
        # 开盘执行 (9:35，避开集合竞价)
        schedule.every().day.at("09:35").do(self.execute_strategy)
        
        # 盘中止损检查 (每30分钟)
        schedule.every(30).minutes.do(self.check_risk)
        
        # 尾盘检查 (14:50)
        schedule.every().day.at("14:50").do(self.end_of_day)
        
        logger.info("🚀 交易调度器已启动")
        
        while True:
            schedule.run_pending()
            time_module.sleep(1)
    
    def pre_market(self):
        """盘前准备"""
        logger.info("📋 盘前准备...")
        # 检查连接
        if not self.engine.broker._connected:
            self.engine.broker.connect()
        # 下载最新数据
        # 更新股票池
    
    def execute_strategy(self):
        """执行策略"""
        if not self.is_trading_day:
            return
        logger.info("📊 执行策略...")
        # target = self.strategy.generate_target_portfolio(...)
        # self.engine.execute_target_portfolio(target)
    
    def check_risk(self):
        """风控检查"""
        self.engine.check_and_execute_stop_loss()
    
    def end_of_day(self):
        """尾盘处理"""
        report = self.engine.get_execution_report()
        logger.info(f"📈 收盘报告: {report}")
```

---

## 模块四：Streamlit 监控看板

### 完整代码

```python
# ============================================================
#  dashboard.py — Streamlit 量化监控看板
#  运行: streamlit run dashboard.py
# ============================================================

import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots
from datetime import datetime, timedelta
import json
import os

# 页面配置
st.set_page_config(
    page_title="📊 量化策略监控",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded"
)


# ============================================================
#  工具函数
# ============================================================

@st.cache_data(ttl=300)  # 缓存5分钟
def load_equity_curve(path: str = "data/equity_curve.csv") -> pd.DataFrame:
    """加载净值曲线"""
    if os.path.exists(path):
        df = pd.read_csv(path, parse_dates=['date'], index_col='date')
        return df
    # 模拟数据
    dates = pd.date_range('2024-01-01', periods=500, freq='B')
    np.random.seed(42)
    returns = np.random.randn(500) * 0.015 + 0.0003
    equity = 1_000_000 * np.cumprod(1 + returns)
    benchmark = 1_000_000 * np.cumprod(1 + np.random.randn(500) * 0.012 + 0.0002)
    return pd.DataFrame({'equity': equity, 'benchmark': benchmark}, index=dates)


@st.cache_data(ttl=300)
def load_positions(path: str = "data/positions.json") -> pd.DataFrame:
    """加载持仓"""
    if os.path.exists(path):
        with open(path) as f:
            data = json.load(f)
        return pd.DataFrame(data)
    # 模拟数据
    return pd.DataFrame({
        'code': ['000001.SZ', '600519.SH', '000858.SZ', '601318.SH', '002415.SZ'],
        'name': ['平安银行', '贵州茅台', '五粮液', '中国平安', '海康威视'],
        'volume': [5000, 100, 800, 2000, 1500],
        'cost_price': [12.5, 1680.0, 145.0, 48.0, 32.0],
        'current_price': [13.2, 1720.0, 152.0, 51.5, 34.5],
        'industry': ['银行', '白酒', '白酒', '保险', '电子'],
        'weight': [0.08, 0.22, 0.15, 0.13, 0.07]
    })


@st.cache_data(ttl=300)
def load_factor_ic(path: str = "data/factor_ic.csv") -> pd.DataFrame:
    """加载因子IC"""
    if os.path.exists(path):
        return pd.read_csv(path, parse_dates=['date'], index_col='date')
    # 模拟数据
    dates = pd.date_range('2024-01-01', periods=200, freq='W')
    np.random.seed(42)
    return pd.DataFrame({
        'EP': np.random.randn(200) * 0.05 + 0.03,
        'ROE': np.random.randn(200) * 0.04 + 0.04,
        'Momentum': np.random.randn(200) * 0.06 + 0.01,
        'Volatility': np.random.randn(200) * 0.03 - 0.02,
        'SmallCap': np.random.randn(200) * 0.07 + 0.02,
    }, index=dates)


@st.cache_data(ttl=300)
def load_trade_log(path: str = "data/trades.csv") -> pd.DataFrame:
    """加载交易记录"""
    if os.path.exists(path):
        return pd.read_csv(path, parse_dates=['date'])
    # 模拟数据
    np.random.seed(42)
    n = 100
    return pd.DataFrame({
        'date': pd.date_range('2024-01-01', periods=n, freq='3B'),
        'code': np.random.choice(['000001.SZ', '600519.SH', '000858.SZ', '601318.SH'], n),
        'action': np.random.choice(['BUY', 'SELL'], n),
        'price': np.random.uniform(10, 100, n).round(2),
        'volume': np.random.randint(1, 50, n) * 100,
        'pnl': np.random.randn(n) * 5000,
    })


# ============================================================
#  侧边栏
# ============================================================

with st.sidebar:
    st.title("📊 量化监控面板")
    st.markdown("---")
    
    page = st.radio(
        "导航",
        ["📈 策略概览", "📊 因子分析", "💼 持仓管理", "📋 交易记录", "⚙️ 系统设置"]
    )
    
    st.markdown("---")
    st.markdown(f"**最后更新**: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    
    if st.button("🔄 刷新数据"):
        st.cache_data.clear()
        st.rerun()


# ============================================================
#  页面1: 策略概览
# ============================================================

if page == "📈 策略概览":
    st.header("📈 策略绩效概览")
    
    equity_df = load_equity_curve()
    
    # 计算指标
    total_return = equity_df['equity'].iloc[-1] / equity_df['equity'].iloc[0] - 1
    daily_returns = equity_df['equity'].pct_change().dropna()
    annual_return = (1 + total_return) ** (252 / len(equity_df)) - 1
    sharpe = daily_returns.mean() / daily_returns.std() * np.sqrt(252)
    cummax = equity_df['equity'].cummax()
    drawdown = (equity_df['equity'] - cummax) / cummax
    max_dd = drawdown.min()
    
    # 指标卡片
    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("总收益率", f"{total_return:.2%}")
    col2.metric("年化收益", f"{annual_return:.2%}")
    col3.metric("夏普比率", f"{sharpe:.3f}")
    col4.metric("最大回撤", f"{max_dd:.2%}")
    col5.metric("年化波动", f"{daily_returns.std() * np.sqrt(252):.2%}")
    
    st.markdown("---")
    
    # 净值曲线
    fig = make_subplots(
        rows=2, cols=1,
        shared_xaxes=True,
        vertical_spacing=0.05,
        row_heights=[0.7, 0.3],
        subplot_titles=("净值曲线", "回撤")
    )
    
    fig.add_trace(go.Scatter(
        x=equity_df.index, y=equity_df['equity'],
        name='策略净值', line=dict(color='#2196F3', width=2)
    ), row=1, col=1)
    
    if 'benchmark' in equity_df.columns:
        fig.add_trace(go.Scatter(
            x=equity_df.index, y=equity_df['benchmark'],
            name='基准(沪深300)', line=dict(color='#9E9E9E', width=1.5, dash='dash')
        ), row=1, col=1)
    
    fig.add_trace(go.Scatter(
        x=drawdown.index, y=drawdown,
        name='回撤', fill='tozeroy',
        line=dict(color='#F44336', width=1)
    ), row=2, col=1)
    
    fig.update_layout(height=600, showlegend=True, hovermode='x unified')
    st.plotly_chart(fig, use_container_width=True)
    
    # 月度收益热力图
    st.subheader("📅 月度收益")
    monthly_returns = daily_returns.resample('M').apply(lambda x: (1+x).prod() - 1)
    monthly_df = pd.DataFrame({
        'year': monthly_returns.index.year,
        'month': monthly_returns.index.month,
        'return': monthly_returns.values
    })
    pivot = monthly_df.pivot(index='year', columns='month', values='return')
    pivot.columns = [f'{m}月' for m in pivot.columns]
    
    fig_heatmap = px.imshow(
        pivot, text_auto='.1%',
        color_continuous_scale='RdYlGn',
        aspect='auto'
    )
    fig_heatmap.update_layout(height=300)
    st.plotly_chart(fig_heatmap, use_container_width=True)


# ============================================================
#  页面2: 因子分析
# ============================================================

elif page == "📊 因子分析":
    st.header("📊 因子有效性分析")
    
    ic_df = load_factor_ic()
    
    # IC统计
    st.subheader("IC统计")
    ic_stats = pd.DataFrame({
        'IC均值': ic_df.mean(),
        'IC标准差': ic_df.std(),
        'ICIR': ic_df.mean() / ic_df.std(),
        'IC>0占比': (ic_df > 0).mean(),
        '|IC|>0.03': (ic_df.abs() > 0.03).mean(),
    }).round(4)
    st.dataframe(ic_stats.style.background_gradient(cmap='RdYlGn', axis=1))
    
    # IC时序图
    st.subheader("IC时序")
    selected_factors = st.multiselect(
        "选择因子", ic_df.columns.tolist(), 
        default=ic_df.columns[:3].tolist()
    )
    
    fig_ic = go.Figure()
    for factor in selected_factors:
        fig_ic.add_trace(go.Scatter(
            x=ic_df.index, y=ic_df[factor],
            name=factor, mode='lines',
            line=dict(width=1.5)
        ))
        # 添加滚动均值
        fig_ic.add_trace(go.Scatter(
            x=ic_df.index, y=ic_df[factor].rolling(12).mean(),
            name=f'{factor}_MA12', mode='lines',
            line=dict(width=2.5)
        ))
    
    fig_ic.add_hline(y=0, line_dash="dash", line_color="gray")
    fig_ic.update_layout(height=400, hovermode='x unified')
    st.plotly_chart(fig_ic, use_container_width=True)
    
    # 因子相关性
    st.subheader("因子相关性矩阵")
    corr = ic_df.corr()
    fig_corr = px.imshow(
        corr, text_auto='.2f',
        color_continuous_scale='RdBu_r',
        zmin=-1, zmax=1
    )
    st.plotly_chart(fig_corr, use_container_width=True)


# ============================================================
#  页面3: 持仓管理
# ============================================================

elif page == "💼 持仓管理":
    st.header("💼 当前持仓")
    
    positions = load_positions()
    
    # 汇总指标
    total_mv = (positions['current_price'] * positions['volume']).sum()
    total_cost = (positions['cost_price'] * positions['volume']).sum()
    total_pnl = total_mv - total_cost
    total_pnl_pct = total_pnl / total_cost
    
    col1, col2, col3 = st.columns(3)
    col1.metric("持仓市值", f"¥{total_mv:,.0f}")
    col2.metric("总盈亏", f"¥{total_pnl:,.0f}", delta=f"{total_pnl_pct:.2%}")
    col3.metric("持仓数量", f"{len(positions)} 只")
    
    st.markdown("---")
    
    # 持仓明细表
    positions['盈亏'] = (positions['current_price'] - positions['cost_price']) * positions['volume']
    positions['盈亏比例'] = positions['current_price'] / positions['cost_price'] - 1
    positions['市值'] = positions['current_price'] * positions['volume']
    
    st.dataframe(
        positions.style.format({
            'cost_price': '¥{:.2f}',
            'current_price': '¥{:.2f}',
            '市值': '¥{:,.0f}',
            '盈亏': '¥{:,.0f}',
            '盈亏比例': '{:.2%}'
        }).background_gradient(subset=['盈亏比例'], cmap='RdYlGn'),
        use_container_width=True
    )
    
    # 行业分布饼图
    col1, col2 = st.columns(2)
    
    with col1:
        st.subheader("行业分布")
        industry_mv = positions.groupby('industry')['市值'].sum()
        fig_pie = px.pie(
            values=industry_mv.values,
            names=industry_mv.index,
            hole=0.4
        )
        st.plotly_chart(fig_pie, use_container_width=True)
    
    with col2:
        st.subheader("个股权重")
        fig_bar = px.bar(
            positions, x='name', y='weight',
            color='盈亏比例',
            color_continuous_scale='RdYlGn'
        )
        fig_bar.update_yaxes(tickformat='.0%')
        st.plotly_chart(fig_bar, use_container_width=True)


# ============================================================
#  页面4: 交易记录
# ============================================================

elif page == "📋 交易记录":
    st.header("📋 交易记录")
    
    trades = load_trade_log()
    
    # 筛选
    col1, col2, col3 = st.columns(3)
    with col1:
        action_filter = st.selectbox("方向", ["全部", "BUY", "SELL"])
    with col2:
        date_range = st.date_input(
            "日期范围",
            value=(trades['date'].min(), trades['date'].max())
        )
    with col3:
        code_filter = st.text_input("股票代码（留空=全部）")
    
    # 过滤
    filtered = trades.copy()
    if action_filter != "全部":
        filtered = filtered[filtered['action'] == action_filter]
    if code_filter:
        filtered = filtered[filtered['code'].str.contains(code_filter)]
    
    st.dataframe(filtered, use_container_width=True)
    
    # 交易统计
    st.subheader("交易统计")
    sell_trades = trades[trades['action'] == 'SELL']
    if not sell_trades.empty:
        win_rate = (sell_trades['pnl'] > 0).mean()
        avg_win = sell_trades[sell_trades['pnl'] > 0]['pnl'].mean() if (sell_trades['pnl'] > 0).any() else 0
        avg_loss = sell_trades[sell_trades['pnl'] < 0]['pnl'].mean() if (sell_trades['pnl'] < 0).any() else 0
        profit_factor = abs(sell_trades[sell_trades['pnl'] > 0]['pnl'].sum() / 
                          sell_trades[sell_trades['pnl'] < 0]['pnl'].sum()) if (sell_trades['pnl'] < 0).any() else float('inf')
        
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("胜率", f"{win_rate:.1%}")
        col2.metric("平均盈利", f"¥{avg_win:,.0f}")
        col3.metric("平均亏损", f"¥{avg_loss:,.0f}")
        col4.metric("盈亏比", f"{profit_factor:.2f}")


# ============================================================
#  页面5: 系统设置
# ============================================================

elif page == "⚙️ 系统设置":
    st.header("⚙️ 系统设置")
    
    col1, col2 = st.columns(2)
    
    with col1:
        st.subheader("策略参数")
        rebalance_freq = st.selectbox("调仓频率", ["daily", "weekly", "monthly"], index=1)
        max_stocks = st.slider("最大持仓数", 10, 100, 30)
        stop_loss = st.slider("止损线", 0.03, 0.15, 0.08, 0.01)
        risk_aversion = st.slider("风险厌恶系数", 0.5, 5.0, 2.0, 0.5)
    
    with col2:
        st.subheader("风控参数")
        max_position = st.slider("单股最大仓位", 0.02, 0.10, 0.05, 0.01)
        max_industry = st.slider("单行业最大暴露", 0.10, 0.40, 0.20, 0.05)
        max_drawdown = st.slider("组合最大回撤", 0.05, 0.30, 0.15, 0.01)
        max_turnover = st.slider("最大换手率", 0.10, 0.50, 0.30, 0.05)
    
    if st.button("💾 保存配置"):
        config = {
            "rebalance_freq": rebalance_freq,
            "max_stocks": max_stocks,
            "stop_loss": stop_loss,
            "risk_aversion": risk_aversion,
            "max_position": max_position,
            "max_industry": max_industry,
            "max_drawdown": max_drawdown,
            "max_turnover": max_turnover,
        }
        with open("config/settings.json", "w") as f:
            json.dump(config, f, indent=2)
        st.success("✅ 配置已保存")
    
    st.markdown("---")
    st.subheader("系统状态")
    st.info(f"""
    - 数据源: Tushare Pro ✅
    - miniQMT: 已连接 ✅
    - 最后调仓: 2026-07-18 (周五)
    - 下次调仓: 2026-07-25 (周五)
    - 运行天数: 186天
    """)
```

### 启动命令

```bash
# 安装依赖
pip install streamlit plotly pandas numpy

# 启动看板
streamlit run dashboard.py --server.port 8501
```

---

## 模块五：舆情因子（NLP情绪分析）

### 架构

```
新闻/公告/社交媒体 → 数据采集 → NLP情绪分析 → 情绪因子 → 合入多因子模型
     │                  │              │              │
  财联社/东财       定时爬取      FinBERT/       情绪得分
  雪球/股吧        API接口      中文情感模型    关注度因子
  上交所公告                    关键词匹配      异常情绪
```

### 完整代码

```python
import pandas as pd
import numpy as np
from typing import List, Dict, Optional
from dataclasses import dataclass
from datetime import datetime, timedelta
import re
import logging

logger = logging.getLogger("Sentiment")


# ============================================================
#  1. 数据采集层
# ============================================================

@dataclass
class NewsItem:
    """新闻/公告数据"""
    title: str
    content: str
    source: str          # 来源: eastmoney / cls / sse / xueqiu
    publish_time: datetime
    related_codes: List[str]  # 关联股票代码
    url: str = ""


class NewsCollector:
    """新闻采集器"""
    
    def __init__(self):
        self.sources = {}
    
    def collect_eastmoney(self, code: str, days: int = 7) -> List[NewsItem]:
        """
        采集东方财富个股新闻
        """
        import requests
        
        news_list = []
        # 东财个股新闻API
        url = f"https://search-api-web.eastmoney.com/search/jsonp"
        params = {
            'param': f'{{"uid":"","keyword":"{code}","type":["cmsArticleWebOld"],'
                     f'"client":"web","clientType":"web","clientVersion":"curr",'
                     f'"param":{{"cmsArticleWebOld":{{"searchScope":"default",'
                     f'"sort":"default","pageIndex":1,"pageSize":50}}}}}}'
        }
        
        try:
            resp = requests.get(url, params=params, timeout=10)
            data = resp.json()
            
            for item in data.get('result', {}).get('cmsArticleWebOld', {}).get('list', []):
                news_list.append(NewsItem(
                    title=item.get('title', ''),
                    content=item.get('content', ''),
                    source='eastmoney',
                    publish_time=datetime.strptime(item['date'], '%Y-%m-%d %H:%M:%S'),
                    related_codes=[code],
                    url=item.get('url', '')
                ))
        except Exception as e:
            logger.warning(f"东财采集失败 {code}: {e}")
        
        return news_list
    
    def collect_cls(self, days: int = 1) -> List[NewsItem]:
        """
        采集财联社电报（快讯）
        """
        import requests
        
        news_list = []
        url = "https://www.cls.cn/nodeapi/updateTelegraphList"
        params = {'rn': 50, 'os': 'web', 'sv': '7.7.5'}
        
        try:
            resp = requests.get(url, params=params, timeout=10)
            data = resp.json()
            
            for item in data.get('data', {}).get('roll_data', []):
                news_list.append(NewsItem(
                    title=item.get('title', '') or item.get('brief', ''),
                    content=item.get('content', ''),
                    source='cls',
                    publish_time=datetime.fromtimestamp(item.get('ctime', 0)),
                    related_codes=item.get('stocks', []),
                ))
        except Exception as e:
            logger.warning(f"财联社采集失败: {e}")
        
        return news_list
    
    def collect_xueqiu(self, code: str, days: int = 3) -> List[NewsItem]:
        """
        采集雪球讨论（需cookie）
        """
        import requests
        
        news_list = []
        # 雪球需要登录cookie
        headers = {
            'Cookie': 'your_cookie_here',
            'User-Agent': 'Mozilla/5.0'
        }
        
        symbol = code.split('.')[0]
        url = f"https://xueqiu.com/query/v1/symbol/search/status.json"
        params = {
            'symbol': symbol,
            'count': 50,
            'comment': 0,
            'hl': 0,
            'source': 'all',
            'sort': 'time',
        }
        
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=10)
            data = resp.json()
            
            for item in data.get('list', []):
                news_list.append(NewsItem(
                    title=item.get('title', '') or item.get('description', '')[:100],
                    content=item.get('description', ''),
                    source='xueqiu',
                    publish_time=datetime.fromtimestamp(item.get('created_at', 0) / 1000),
                    related_codes=[code],
                ))
        except Exception as e:
            logger.warning(f"雪球采集失败 {code}: {e}")
        
        return news_list
    
    def collect_sse_announcement(self, code: str, days: int = 30) -> List[NewsItem]:
        """
        采集上交所/深交所公告
        """
        import requests
        
        news_list = []
        # 巨潮资讯API
        url = "http://www.cninfo.com.cn/new/hisAnnouncement/query"
        data = {
            'stock': code.split('.')[0],
            'pageNum': 1,
            'pageSize': 30,
            'column': 'szse',  # 或 'sse'
            'category': '',
            'plate': '',
            'seDate': '',
        }
        
        try:
            resp = requests.post(url, data=data, timeout=10)
            result = resp.json()
            
            for item in result.get('announcements', []):
                news_list.append(NewsItem(
                    title=item.get('announcementTitle', ''),
                    content='',  # 公告内容需下载PDF
                    source='cninfo',
                    publish_time=datetime.strptime(
                        item['announcementTime'], '%Y-%m-%d'
                    ) if 'announcementTime' in item else datetime.now(),
                    related_codes=[code],
                ))
        except Exception as e:
            logger.warning(f"公告采集失败 {code}: {e}")
        
        return news_list


# ============================================================
#  2. NLP情绪分析层
# ============================================================

class SentimentAnalyzer:
    """
    情绪分析器
    
    支持三种模式:
    1. 关键词词典（轻量、快速）
    2. FinBERT（精准、需GPU）
    3. 大模型API（最精准、有成本）
    """
    
    def __init__(self, method: str = "keyword"):
        """
        method: "keyword" / "finbert" / "llm"
        """
        self.method = method
        self._model = None
        self._tokenizer = None
        
        # 金融情感词典
        self.positive_words = {
            '增长', '上涨', '涨停', '利好', '突破', '创新高', '超预期',
            '回购', '增持', '分红', '盈利', '复苏', '景气', '扩张',
            '中标', '签约', '合作', '获批', '通过', '上调', '买入',
            '推荐', '看好', '龙头', '领先', '创新', '突破', '加速',
        }
        self.negative_words = {
            '下跌', '跌停', '利空', '亏损', '下滑', '减持', '质押',
            '违规', '处罚', '退市', '暴雷', '商誉减值', '诉讼',
            '下调', '卖出', '风险', '警示', 'ST', '暂停', '终止',
            '低于预期', '不及预期', '萎缩', '衰退', '破产', '违约',
        }
        self.intensifiers = {
            '大幅', '显著', '急剧', '严重', '重大', '强烈',
            '持续', '连续', '突然', '意外', '罕见', '历史',
        }
    
    def analyze_keyword(self, text: str) -> Dict:
        """
        基于词典的情绪分析
        
        返回: {score: float, positive_count: int, negative_count: int}
        """
        if not text:
            return {'score': 0.0, 'positive_count': 0, 'negative_count': 0}
        
        pos_count = sum(1 for w in self.positive_words if w in text)
        neg_count = sum(1 for w in self.negative_words if w in text)
        
        # 检查强化词
        intensity = 1.0
        for w in self.intensifiers:
            if w in text:
                intensity = 1.5
                break
        
        total = pos_count + neg_count
        if total == 0:
            score = 0.0
        else:
            score = (pos_count - neg_count) / total * intensity
        
        return {
            'score': np.clip(score, -1, 1),
            'positive_count': pos_count,
            'negative_count': neg_count,
            'intensity': intensity
        }
    
    def analyze_finbert(self, text: str) -> Dict:
        """
        基于FinBERT的情绪分析（需GPU）
        
        模型: chinese-roberta-wwm-ext-large (金融领域微调)
        或: yiyanghkust/finbert-tone (英文)
        """
        if self._model is None:
            from transformers import AutoTokenizer, AutoModelForSequenceClassification
            import torch
            
            model_name = "ugursa/Yahoo-Finance-Sentiment-Sentences"
            # 中文推荐: "uer/roberta-base-finetuned-chinanews-chinese"
            self._tokenizer = AutoTokenizer.from_pretrained(model_name)
            self._model = AutoModelForSequenceClassification.from_pretrained(model_name)
            self._model.eval()
        
        import torch
        
        inputs = self._tokenizer(
            text, return_tensors="pt", 
            truncation=True, max_length=512
        )
        
        with torch.no_grad():
            outputs = self._model(**inputs)
            probs = torch.softmax(outputs.logits, dim=-1).numpy()[0]
        
        # 假设: [negative, neutral, positive]
        score = probs[2] - probs[0]  # positive - negative
        
        return {
            'score': float(score),
            'positive_prob': float(probs[2]),
            'neutral_prob': float(probs[1]),
            'negative_prob': float(probs[0]),
        }
    
    def analyze_llm(self, text: str) -> Dict:
        """
        基于大模型API的情绪分析（最精准）
        
        支持: OpenAI / 通义千问 / 文心一言
        """
        import requests
        
        prompt = f"""
        请分析以下金融文本的情绪倾向，返回JSON格式:
        {{"score": -1到1的浮点数, "reason": "简要原因"}}
        
        -1表示极度负面，0表示中性，1表示极度正面
        
        文本: {text[:500]}
        """
        
        # 以通义千问为例
        try:
            resp = requests.post(
                "https://dashscope.aliyuncs.com/api/v1/services/aigc/text-generation/generation",
                headers={"Authorization": "Bearer your_api_key"},
                json={
                    "model": "qwen-turbo",
                    "input": {"messages": [{"role": "user", "content": prompt}]}
                },
                timeout=30
            )
            result = resp.json()
            content = result['output']['text']
            
            # 解析JSON
            import json
            parsed = json.loads(re.search(r'\{.*\}', content).group())
            return {
                'score': float(parsed.get('score', 0)),
                'reason': parsed.get('reason', '')
            }
        except Exception as e:
            logger.warning(f"LLM分析失败: {e}")
            return {'score': 0.0, 'reason': 'error'}
    
    def analyze(self, text: str) -> Dict:
        """统一分析入口"""
        if self.method == "keyword":
            return self.analyze_keyword(text)
        elif self.method == "finbert":
            return self.analyze_finbert(text)
        elif self.method == "llm":
            return self.analyze_llm(text)
        return {'score': 0.0}


# ============================================================
#  3. 舆情因子构建
# ============================================================

class SentimentFactorBuilder:
    """
    舆情因子构建器
    
    输出因子:
    1. sentiment_score: 情绪得分（近N日新闻平均情绪）
    2. attention_score: 关注度（新闻数量/讨论热度）
    3. sentiment_momentum: 情绪动量（情绪变化趋势）
    4. abnormal_sentiment: 异常情绪（偏离历史均值）
    5. news_volume_ratio: 新闻量比率（相对历史）
    """
    
    def __init__(self, analyzer: SentimentAnalyzer,
                 window: int = 7):
        self.analyzer = analyzer
        self.window = window
    
    def build_factors(self, news_data: Dict[str, List[NewsItem]],
                      date: str) -> pd.DataFrame:
        """
        构建舆情因子截面
        
        news_data: {code: [NewsItem, ...]}
        
        返回: DataFrame (index=code, columns=因子名)
        """
        records = []
        
        for code, news_list in news_data.items():
            if not news_list:
                records.append({
                    'code': code,
                    'sentiment_score': 0.0,
                    'attention_score': 0.0,
                    'sentiment_momentum': 0.0,
                    'abnormal_sentiment': 0.0,
                    'news_volume_ratio': 1.0,
                })
                continue
            
            # 分析每条新闻
            scores = []
            for news in news_list:
                text = f"{news.title} {news.content}"
                result = self.analyzer.analyze(text)
                scores.append(result['score'])
            
            scores = np.array(scores)
            
            # 因子1: 情绪得分（加权平均，越近权重越大）
            if len(scores) > 0:
                weights = np.exp(np.linspace(-1, 0, len(scores)))
                weights /= weights.sum()
                sentiment_score = np.average(scores, weights=weights)
            else:
                sentiment_score = 0.0
            
            # 因子2: 关注度（新闻数量的对数）
            attention_score = np.log1p(len(news_list))
            
            # 因子3: 情绪动量（近期 vs 远期）
            if len(scores) >= 4:
                mid = len(scores) // 2
                recent = scores[mid:].mean()
                earlier = scores[:mid].mean()
                sentiment_momentum = recent - earlier
            else:
                sentiment_momentum = 0.0
            
            # 因子4: 异常情绪（偏离0的程度）
            abnormal_sentiment = abs(sentiment_score) if len(scores) > 0 else 0.0
            
            # 因子5: 新闻量比率（简化：用绝对数量）
            news_volume_ratio = len(news_list) / max(self.window, 1)
            
            records.append({
                'code': code,
                'sentiment_score': sentiment_score,
                'attention_score': attention_score,
                'sentiment_momentum': sentiment_momentum,
                'abnormal_sentiment': abnormal_sentiment,
                'news_volume_ratio': news_volume_ratio,
            })
        
        return pd.DataFrame(records).set_index('code')
    
    def build_event_factor(self, news_data: Dict[str, List[NewsItem]]) -> pd.DataFrame:
        """
        事件驱动因子
        
        检测特定事件:
        - 业绩预告/快报
        - 大股东增减持
        - 回购/分红
        - 重大合同/中标
        - 监管处罚
        """
        event_keywords = {
            '业绩预增': ['预增', '业绩大幅', '净利润增长', '超预期'],
            '业绩预减': ['预减', '业绩下滑', '净利润下降', '低于预期', '亏损'],
            '股东增持': ['增持', '回购', '买入自家'],
            '股东减持': ['减持', '套现', '抛售'],
            '重大合同': ['中标', '签约', '合同', '订单'],
            '监管风险': ['处罚', '违规', '立案', '调查', '警示'],
        }
        
        records = []
        for code, news_list in news_data.items():
            event_scores = {k: 0 for k in event_keywords}
            
            for news in news_list:
                text = f"{news.title} {news.content}"
                for event, keywords in event_keywords.items():
                    if any(kw in text for kw in keywords):
                        event_scores[event] += 1
            
            # 综合事件得分
            positive_events = event_scores['业绩预增'] + event_scores['股东增持'] + event_scores['重大合同']
            negative_events = event_scores['业绩预减'] + event_scores['股东减持'] + event_scores['监管风险']
            
            records.append({
                'code': code,
                'event_score': positive_events - negative_events,
                **event_scores
            })
        
        return pd.DataFrame(records).set_index('code')


# ============================================================
#  4. 舆情因子合入多因子模型
# ============================================================

class SentimentEnhancedStrategy:
    """
    舆情增强多因子策略
    
    将舆情因子与传统因子合并
    """
    
    def __init__(self, factor_engine, sentiment_builder: SentimentFactorBuilder,
                 sentiment_weight: float = 0.15):
        """
        sentiment_weight: 舆情因子在综合得分中的权重
        """
        self.factor_engine = factor_engine
        self.sentiment_builder = sentiment_builder
        self.sentiment_weight = sentiment_weight
    
    def generate_score(self, data: pd.DataFrame,
                       news_data: Dict[str, List[NewsItem]],
                       industry_map: Dict[str, str],
                       date: str) -> pd.Series:
        """生成综合得分（传统因子 + 舆情因子）"""
        
        # 1. 传统因子得分
        factor_df = self.factor_engine.compute_cross_section(data, industry_map, date)
        traditional_score = self.factor_engine.composite_score(factor_df)
        
        # 2. 舆情因子得分
        sentiment_df = self.sentiment_builder.build_factors(news_data, date)
        
        # 标准化舆情因子
        sentiment_score = pd.Series(0.0, index=traditional_score.index)
        if 'sentiment_score' in sentiment_df.columns:
            s = sentiment_df['sentiment_score'].reindex(traditional_score.index).fillna(0)
            s = (s - s.mean()) / (s.std() + 1e-8)
            sentiment_score = s
        
        # 3. 加权合成
        final_score = (
            (1 - self.sentiment_weight) * traditional_score +
            self.sentiment_weight * sentiment_score
        )
        
        return final_score


# ============================================================
#  5. 定时采集调度
# ============================================================

class SentimentScheduler:
    """舆情数据定时采集"""
    
    def __init__(self, collector: NewsCollector,
                 stock_pool: List[str],
                 output_dir: str = "data/sentiment"):
        self.collector = collector
        self.stock_pool = stock_pool
        self.output_dir = output_dir
        os.makedirs(output_dir, exist_ok=True)
    
    def daily_collect(self):
        """每日采集（建议盘后18:00运行）"""
        import os
        
        all_news = {}
        
        # 采集个股新闻
        for code in self.stock_pool:
            news = self.collector.collect_eastmoney(code, days=1)
            all_news[code] = news
            time_module.sleep(0.5)  # 避免频率限制
        
        # 采集市场快讯
        cls_news = self.collector.collect_cls(days=1)
        
        # 保存
        today = datetime.now().strftime('%Y-%m-%d')
        output_path = f"{self.output_dir}/news_{today}.json"
        
        # 序列化
        serializable = {}
        for code, news_list in all_news.items():
            serializable[code] = [
                {
                    'title': n.title,
                    'content': n.content[:500],
                    'source': n.source,
                    'time': n.publish_time.isoformat(),
                }
                for n in news_list
            ]
        
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(serializable, f, ensure_ascii=False, indent=2)
        
        logger.info(f"✅ 舆情数据采集完成: {len(all_news)}只股票, 保存至 {output_path}")
    
    def start_scheduler(self):
        """启动定时任务"""
        import schedule
        
        # 每日18:00采集
        schedule.every().day.at("18:00").do(self.daily_collect)
        
        # 盘中每2小时采集一次快讯
        schedule.every(2).hours.do(lambda: self.collector.collect_cls(days=1))
        
        logger.info("🚀 舆情采集调度器已启动")
        while True:
            schedule.run_pending()
            time_module.sleep(60)


# ============================================================
#  使用示例
# ============================================================

def sentiment_pipeline_example():
    """舆情因子完整流水线"""
    
    # 1. 初始化
    collector = NewsCollector()
    analyzer = SentimentAnalyzer(method="keyword")  # 轻量模式
    builder = SentimentFactorBuilder(analyzer, window=7)
    
    # 2. 采集数据
    stock_pool = ['000001.SZ', '600519.SH', '000858.SZ']
    news_data = {}
    for code in stock_pool:
        news_data[code] = collector.collect_eastmoney(code, days=7)
    
    # 3. 构建因子
    sentiment_factors = builder.build_factors(news_data, "2026-07-21")
    event_factors = builder.build_event_factor(news_data)
    
    print("📊 舆情因子:")
    print(sentiment_factors)
    print("\n📊 事件因子:")
    print(event_factors)
    
    # 4. 合入策略
    # strategy = SentimentEnhancedStrategy(
    #     factor_engine=engine,
    #     sentiment_builder=builder,
    #     sentiment_weight=0.15
    # )
    # final_score = strategy.generate_score(data, news_data, industry_map, date)
```

---

## 六、完整项目结构（最终版）

```
quant_stock/
├── config/
│   ├── settings.yaml              # 全局配置
│   ├── factors.yaml               # 因子配置
│   └── settings.json              # 看板配置（自动生成）
│
├── data/
│   ├── feed.py                    # 数据源（Tushare/AKShare）
│   ├── universe.py                # 股票池
│   ├── cache/                     # 本地缓存
│   ├── sentiment/                 # 舆情数据
│   ├── equity_curve.csv           # 净值曲线
│   ├── positions.json             # 持仓
│   └── trades.csv                 # 交易记录
│
├── factors/
│   ├── base.py                    # 因子基类 + 预处理
│   ├── value.py                   # 估值因子
│   ├── quality.py                 # 质量因子
│   ├── growth.py                  # 成长因子
│   ├── momentum.py                # 动量因子
│   ├── sentiment.py               # 🆕 舆情因子
│   ├── ml_factor.py               # 🆕 ML因子挖掘
│   └── engine.py                  # 因子引擎
│
├── strategy/
│   ├── multi_factor.py            # 多因子选股
│   ├── ml_strategy.py             # 🆕 ML增强策略
│   └── portfolio.py               # 组合构建
│
├── optimization/
│   ├── optimizer.py               # 🆕 cvxpy组合优化
│   └── covariance.py              # 🆕 协方差估计
│
├── risk/
│   └── manager.py                 # 风控
│
├── backtest/
│   ├── engine.py                  # 回测引擎
│   └── analyzer.py                # 绩效分析
│
├── broker/
│   ├── base.py                    # 🆕 Broker抽象
│   ├── mini_qmt.py                # 🆕 miniQMT实盘
│   ├── ptrade.py                  # 🆕 PTrade实盘
│   ├── paper.py                   # 🆕 模拟盘
│   └── live_engine.py             # 🆕 实盘执行引擎
│
├── sentiment/
│   ├── collector.py               # 🆕 新闻采集
│   ├── analyzer.py                # 🆕 NLP情绪分析
│   └── scheduler.py               # 🆕 定时采集
│
├── dashboard/
│   └── app.py                     # 🆕 Streamlit看板
│
├── main.py                        # 主入口（回测）
├── live_trading.py                # 🆕 实盘入口
├── requirements.txt
└── README.md
```

---

## 七、requirements.txt

```txt
# 核心
pandas>=2.0
numpy>=1.24
scipy>=1.10

# 数据源
tushare>=1.4
akshare>=1.12

# 机器学习
lightgbm>=4.0
xgboost>=2.0
scikit-learn>=1.3

# 组合优化
cvxpy>=1.4
osqp>=0.6

# NLP / 舆情
transformers>=4.35
torch>=2.0
requests>=2.31

# 实盘
xtquant>=1.0        # miniQMT（从券商获取）

# 看板
streamlit>=1.30
plotly>=5.18

# 工具
schedule>=1.2
statsmodels>=0.14
loguru>=0.7
pyyaml>=6.0
```

---

## 八、各模块运行方式

|模块|命令|频率|
|---|---|---|
|回测|`python main.py`|策略研发时|
|实盘|`python live_trading.py`|交易日 9:15-15:00|
|看板|`streamlit run dashboard/app.py`|常驻|
|舆情采集|`python sentiment/scheduler.py`|每日 18:00|
|ML训练|`python factors/ml_factor.py`|每月/每周|

---

## 九、实盘上线检查清单

```
□ 1. 回测夏普 > 1.5，最大回撤 < 15%
□ 2. 样本外（最近6个月）表现与样本内一致
□ 3. 模拟盘运行 ≥ 1个月，无重大偏差
□ 4. miniQMT/PTrade 连接测试通过
□ 5. 止损逻辑验证（手动触发测试）
□ 6. 涨跌停/停牌边界情况测试
□ 7. 网络断线重连机制
□ 8. 资金门槛确认（miniQMT: 10-50万）
□ 9. 初始资金 ≤ 总资产的 20%（试水）
□ 10. 监控告警配置（微信/钉钉通知）
```

---

需要我针对某个具体模块进一步细化（比如 FinBERT 中文金融模型的微调流程、或者 miniQMT 的具体开户对接步骤）吗？

