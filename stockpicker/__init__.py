"""
StockPicker Module
Extracts core logic from standalone StockPicker app for dashboard integration
"""

__version__ = "1.0.0"

from .parameter_advisor import generate_expert_parameter_advice

__all__ = ["generate_expert_parameter_advice"]

