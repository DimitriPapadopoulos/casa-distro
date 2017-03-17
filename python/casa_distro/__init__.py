from __future__ import absolute_import

import os
import os.path as osp

from .info import NAME as project_name, version_major, version_minor
# Find location of the shared directory
share_directory = osp.join(os.environ.get('BRAINVISA_HOME', '/usr'), 'share', '%s-%s.%s' % (project_name, version_minor, version_minor))