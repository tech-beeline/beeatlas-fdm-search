# Copyright (c) 2024 PJSC VimpelCom

import sys
import logging

def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s | %(levelname)s | %(message)s',
        handlers=[logging.StreamHandler(sys.stdout)],
        force=True

    )
