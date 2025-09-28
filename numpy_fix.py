# numpy_fix.py
import sys
import numpy as np

# Patch für alte pandas_ta Versionen
np.NaN = np.nan
np.NAN = np.nan
setattr(np, 'NaN', np.nan)

# Silent fix - no output needed