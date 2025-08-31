#import os
#import sys
#import warnings

#os.environ['SQLALCHEMY_WARN_20'] = 'yes'
#if not sys.warnoptions:
#    warnings.simplefilter("default")

from gi import require_version

require_version("Gdk", "3.0")
require_version("Gst", "1.0")
require_version("Gtk", "3.0")
require_version("Pango", "1.0")
