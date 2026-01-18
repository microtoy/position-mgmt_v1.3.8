# -*- coding: utf-8 -*-
"""
回测网页版 | 邢不行 | 2025分享会
author: 邢不行
微信: xbx6660
"""

from typing import Optional, Any

from pydantic import BaseModel


class ResponseModel(BaseModel):
    msg: str = "success"
    code: int = 200
    data: Optional[Any] = None

    @classmethod
    def ok(cls, data: Any = None, msg: str = "success", code: int = 200):
        return cls(msg=msg, code=code, data=data)

    @classmethod
    def error(cls, msg: str = "error", code: int = 400):
        return cls(msg=msg, code=code, data=None)

    @classmethod
    def fail(cls, msg: str = "error", code: int = 500):
        return cls(msg=msg, code=code, data=None)


class ProductInfo(BaseModel):
    product_name: str
    display_name: Optional[str] = None
    dataContentTime: Optional[str] = None
    lastUpdateTime: Optional[str] = None
    full_status: Optional[str] = None
    update_status: Optional[str] = None

    @property
    def product_daily_name(self):
        return self.product_name + "-daily"

    @classmethod
    def dict_to_product_info(cls, d) -> "ProductInfo":
        """辅助函数，将dict反序列化为ProductInfo对象"""
        if isinstance(d, cls):
            return d
        return cls(**d)


class ToolConfigModel(BaseModel):
    main_factor: Optional[str] = None
    sub_factor: Optional[str] = None
    filter_list: Optional[list] = None
    strategy_info: Optional[dict] = None
    param_search_info: Optional[dict] = None
    strategy_results: Optional[list] = None
    factor_dict: Optional[dict] = None
    mode: Optional[str] = None



class PositionModel(BaseModel):

    """
 strategy_config = {
     'name': 'FixedRatioStrategy',  # *必填。使用什么策略，这里是轮动策略
     'hold_period': '1H',  # *必填。聚合后策略持仓周期。目前回测支持日线级别、小时级别。例：1H，6H，3D，7D......
     'params': {
         'cap_ratios': [0.5, 0.5]
     }
 }
 """
    FixedRatioStrategy: str = '固定比例'
    """
 strategy_config = {
     'name': 'RotationStrategyOffset',  # *必填。使用什么策略，这里是轮动策略
     'hold_period': '1H',  # *必填。聚合后策略持仓周期。目前回测支持日线级别、小时级别。例：1H，6H，3D，7D......
     'params': {
         'factor_list': [
             ('BiasMean', False, 54, 1),
         ],
    }
 }
 """
    RotationStrategy: str = '简单轮动'
    """
strategy_config = {
    'name': 'RotationStrategyOffset',  # *必填。使用什么策略，这里是轮动策略
    'hold_period': '1H',  # *必填。聚合后策略持仓周期。目前回测支持日线级别、小时级别。例：1H，6H，3D，7D......
    'params': {
        'factor_list': [
            ('BiasMean', False, 54, 1),
        ],
        'rotation_period': '1H',  # 新增配置，调整轮动周期
        'offset_list': [0],  # 新增配置，设置执行轮动 offset
    }
}
"""
    RotationStrategyOffset: str = '简单轮动-带offset'
    """
strategy_config = {
    'name': 'MultiRotationStrategy',
    'hold_period': '1H',
    'params': {
        'factor_list': [
            ('Bias', False, 552, 1),
            ('Bias', False, 888, 1),
        ],
        'rotation_period': '6H',
        'offset_list': [4, 3],
        'rotation_group': {
            'group1': {
                'cap_ratio': 0.25,
                'strategy_names': ['普通多空'],
            },
            'group2_1': {
                'cap_ratio': 0.4 / 2,
                'strategy_names': ['浪2多空'],
            },
        },
    },
}
"""
    MultiRotationStrategy: str = '分组轮动'
    """
strategy_config = {
    'name': 'MultiRotationStrategyOverride',
    'hold_period': '1H',
    'params': {
        'factor_list': [
            ('Bias', False, 552, 1),
            ('Bias', False, 888, 1),
        ],
        'rotation_period': '6H',
        'offset_list': [4, 3],
        'rotation_group': {
            'group1': {
                'cap_ratio': 0.25,
                'strategy_names': ['普通多空'],
                'override': {
                    'type': 'empty',
                    'group_names': ['group2'],
                },
            },
            'group2': {
                'cap_ratio': 0.4 / 2,
                'strategy_names': ['浪2多空'],
            },
        },
    },
}
"""
    MultiRotationStrategyOverride: str = '分组轮动-带填充'


class PositionParam(BaseModel):
    name: str
    hold_period: str
    params: dict = dict(
        factor_list=[
            ("Bias", False, 168, 1),
        ],
        rotation_period="6H",
        offset_list=[0, 1, 2, 3, 4, 5],
        rotation_group={
            "group1": {
                "cap_ratio": 0.2,
                "strategy_names": ["普通多空"],
            },
            "group2_1": {
                "cap_ratio": 0.15 / 2,
                "strategy_names": ["浪2多空"],
            },
        },
    )
    symbol_ratio_limit: dict = dict(
        long={},
        short={},
    )
