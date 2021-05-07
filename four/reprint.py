# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

__all__ = ['Reprint', 'reprint']

import io as _io
import threading as _threading

class Reprint():
    def __init__(self, print, output_file):
        self._print = print
        self._output_file = output_file
        self._lock = _threading.Lock()

    def print(self, *args, **kwargs):
        if 'file' in kwargs:
            self._print(*args, **kwargs)
        else:
            with self._lock:
                string_io = _io.StringIO()
                self._print(*args, **kwargs, file=string_io)
                value = string_io.getvalue()
                string_io.close()

                with open(self._output_file, 'a') as f:
                    f.write(value)
                self._print(value, end='')

def reprint(*args, **kwargs):
    return Reprint(*args, **kwargs).print
