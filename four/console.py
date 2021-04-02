# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

__all__ = ['HTTPHandler', 'make_handler', 'start']

import ast as _ast
import secrets as _secrets
import socketserver as _socketserver
import string as _string
import threading as _threading
import traceback as _traceback
from http import server as _server
from urllib.parse import parse_qs as _parse_qs, urlparse as _urlparse

class _History():
    def __init__(self, len):
        self.version = 0
        self.len = max(len, 0)
        self.lock = _threading.Lock()
        self.reset_nolock()

    def reset_nolock(self):
        self.start = 0
        self.value = b''

    def get(self):
        with self.lock:
            return self.version, self.start, self.value

    def append(self, value):
        with self.lock:
            if len(value) > self.len:
                self.start += len(self.value) + (len(value) - self.len)
                self.value = value[-self.len:]
            elif len(value) > self.len - len(self.value):
                advance = len(self.value) - (self.len - len(value))
                self.start += advance
                self.value = self.value[advance:] + value
            else:
                self.value += value

    def reset(self):
        with self.lock:
            self.reset_nolock()
            self.version += 1

class _Executor():
    def __init__(self, handler, globals):
        self.handler = handler
        self.globals = globals
        self.locals = {}

    def exec_nodes(self, nodes, exception):
        try:
            if 'type_ignores' in  _ast.Module._fields:
                compiled = compile(_ast.Module(body=nodes, type_ignores=[]), filename='<executor>', mode='exec')
            else:
                compiled = compile(_ast.Module(body=nodes), filename='<executor>', mode='exec')
            exec(compiled, self.globals, self.locals)
        except:
            raise exception(_traceback.format_exc())

    def eval_node(self, node, exception):
        try:
            compiled = compile(_ast.Expression(body=node.value), filename='<executor>', mode='eval')
            return eval(compiled, self.globals, self.locals)
        except:
            raise exception(_traceback.format_exc())

    def exec_and_eval_ast_nodes(self, nodes, exception):
        if not nodes:
            return ''
        if isinstance(nodes[-1], _ast.Expr):
            if len(nodes) >= 2:
                self.exec_nodes(nodes[:-1], exception)
            return repr(self.eval_node(nodes[-1], exception))
        else:
            self.exec_nodes(nodes, exception)
            return ''

    def exec_context(self, code):
        class ExecuteException(BaseException):
            def __init__(self, tb):
                self.tb = tb

        try:
            nodes = list(_ast.iter_child_nodes(_ast.parse(code)))
            return self.exec_and_eval_ast_nodes(nodes, ExecuteException)
        except ExecuteException as e:
            return e.tb
        except:
            return _traceback.format_exc()

    def exec_and_update_handler(self, code):
        self.handler.history.append(code + b'\n')
        result = self.exec_context(code)
        if result:
            self.handler.history.append((result + '\n').encode('utf-8'))

    def execute(self, code):
        _threading.Thread(target=self.exec_and_update_handler, args=(code,), name='Executor').start()

class HTTPHandler(_server.BaseHTTPRequestHandler):
    def parse_url(self):
        self.url = _urlparse(self.path)
        self.queries = _parse_qs(self.url.query)

    # Requires URL parsed
    def auth(self):
        if 'auth' in self.queries:
            if self.auth_key == self.queries['auth'][-1].encode('utf-8'):
                return
        raise Exception('Auth error')

    def log_message(self, format, *args):
        pass

    def do_GET(self):
        self.handle_methods()

    def do_POST(self):
        self.handle_methods()

    def handle_methods(self):
        try:
            self.handle_methods_cases()
        except KeyboardInterrupt or SystemExit:
            raise
        except:
            return

    def handle_methods_cases(self):
        self.parse_url()
        self.auth()

        if self.url.path == '/':
            self.handle_root()
        elif self.url.path == '/history':
            self.handle_history()
        elif self.url.path == '/code':
            self.handle_code()
        elif self.url.path == '/clear':
            self.handle_clear()
        else:
            self.send_response(404)
            self.end_headers()

    def handle_root(self):
        self.send_response(200)
        self.send_header('content-length', len(self.html_source))
        self.send_header('x-colab-notebook-cache-control', 'no-cache')
        self.end_headers()
        self.wfile.write(self.html_source)

    def handle_history(self):
        length = int(self.queries['len'][-1])
        version = int(self.queries['version'][-1])
        begin = int(self.queries['begin'][-1])

        history_version, history_start, history_value = self.history.get()

        if history_version != version:
            # History was reset
            begin = 0

        if begin < history_start:
            begin = history_start
        elif begin > history_start + len(history_value):
            begin = history_start + len(history_value)
        part = history_value[begin - history_start:begin - history_start + length]

        content = str(history_version).encode('utf-8') + b'\n' + \
                  str(history_start).encode('utf-8') + b'\n' + \
                  str(begin).encode('utf-8') + b'\n' + \
                  part

        self.send_response(200)
        self.send_header('content-length', len(content))
        self.send_header('x-colab-notebook-cache-control', 'no-cache')
        self.end_headers()
        self.wfile.write(content)

    def handle_code(self):
        MAX_LEN = 8192
        if 'content-length' not in self.headers:
            return
        length = int(self.headers['content-length'])
        if length > MAX_LEN:
            return

        data = b''
        while len(data) < length:
            buf = self.rfile.read(length - len(data))
            if not buf:
                return
            data += buf

        self.executor.execute(data)

        self.send_response(200)
        self.send_header('content-length', '0')
        self.send_header('x-colab-notebook-cache-control', 'no-cache')
        self.end_headers()

    def handle_clear(self):
        self.history.reset()

        self.send_response(200)
        self.send_header('content-length', '0')
        self.send_header('x-colab-notebook-cache-control', 'no-cache')
        self.end_headers()

def make_handler(globals):
    class Handler(HTTPHandler):
        pass

    def get_secret():
        alphabet = _string.ascii_letters + _string.digits
        return ''.join(_secrets.choice(alphabet) for i in range(24)).encode('utf-8')

    Handler.history = _History(524288)
    Handler.auth_key = get_secret()
    Handler.root_path = '/?auth=' + Handler.auth_key.decode('utf-8')
    Handler.executor = _Executor(Handler, globals)
    Handler.html_source = br'''<!DOCTYPE html>
<html>
<head>
<script>
var authKey = "''' + Handler.auth_key + br'''";
var historyVersion = 0;
var historyStart = 0;
var historyData = new Uint8Array();
var historyText = document.createTextNode('');

var historyDaemonInterval = 0;
var historyDaemonId = null;

function sendCode(code, onsuccess, onerror) {
    var req = new XMLHttpRequest();
    req.addEventListener('load', function(event) {
        onsuccess(req.status);
    });
    req.addEventListener('error', function(event) {
        onerror();
    });
    req.addEventListener('timeout', function(event) {
        onerror();
    });
    req.open('POST', '/code?auth=' + authKey);
    req.timeout = 30000;
    req.send(code);
}

function sendClear(onsuccess, onerror) {
    var req = new XMLHttpRequest();
    req.addEventListener('load', function(event) {
        onsuccess(req.status);
    });
    req.addEventListener('error', function(event) {
        onerror();
    });
    req.addEventListener('timeout', function(event) {
        onerror();
    });
    req.open('POST', '/clear?auth=' + authKey);
    req.timeout = 30000;
    req.send();
}

function getHistory(onsuccess, onerror) {
    var req = new XMLHttpRequest();
    req.addEventListener('load', function(event) {
        onsuccess(req.status, req.response);
    });
    req.addEventListener('error', function(event) {
        onerror();
    });
    req.addEventListener('timeout', function(event) {
        onerror();
    });
    req.open('GET', '/history?auth=' + authKey + '&len=32768&version=' +
             historyVersion + '&begin=' + (historyStart + historyData.length));
    req.responseType = 'arraybuffer';
    req.timeout = 30000;
    req.send();
}

function updateHistory(status, response) {
    if (status != 200) return 1;

    var newVersion, newStart, begin, part;

    var data = new Uint8Array(response);
    var last = 0, i = 0;
    for (var v = 0; v < 3; v++) {
        for (; i < data.length; i++) {
            if (data[i] == '\n'.charCodeAt(0)) {
                var slice = new Uint8Array(data.buffer.slice(last, i));
                var p = parseInt(new TextDecoder().decode(slice), 10);
                if (v == 0) newVersion = p;
                else if (v == 1) newStart = p;
                else if (v == 2) begin = p;

                i++;
                last = i;
                break;
            }
        }
        if (v == 2) part = new Uint8Array(data.buffer.slice(last));
    }

    if (!(newVersion >= 0 && newStart >= 0 && begin >= 0 && begin >= newStart))
        return 1;

    if (newVersion < historyVersion) {
        // Stale response
        return 0;
    } else if (newVersion == historyVersion) {
        var ret = 1;

        if (newStart > historyStart) {
            if (newStart < historyStart + historyData.length)
                historyData = new Uint8Array(historyData.buffer.slice(newStart - historyStart));
            else
                historyData = new Uint8Array();
            historyStart = newStart;

            ret = -1;
        }

        if (begin + part.length <= historyStart + historyData.length) return ret;
        if (begin < historyStart + historyData.length) {
            part = new Uint8Array(part.buffer.slice(historyStart + historyData.length - begin));
            begin = historyStart + historyData.length;
        }
        if (begin > historyStart + historyData.length) return ret;

        newData = new Uint8Array(historyData.length + part.length);
        newData.set(historyData);
        newData.set(part, historyData.length);
        historyData = newData;
        historyText.textContent = new TextDecoder().decode(historyData);
        return -1;
    } else {
        // New version
        if (begin != newStart) return 0;
        historyVersion = newVersion;
        historyStart = newStart;
        historyData = part;
        historyText.textContent = new TextDecoder().decode(historyData);
        return -1;
    }
}

function resetHistoryDaemonTimer() {
    historyDaemonInterval = 0;
    if (historyDaemonId != null) clearTimeout(historyDaemonId);
    historyDaemonId = setTimeout(historyDaemon, historyDaemonInterval);
}

function historyDaemon() {
    if (historyDaemonInterval < 400)
        historyDaemonInterval += 100;
    else if (historyDaemonInterval < 1000)
        historyDaemonInterval += 200;
    else
        historyDaemonInterval += 500;

    if (historyDaemonInterval > 5000) historyDaemonInterval = 5000;
    historyDaemonId = setTimeout(historyDaemon, historyDaemonInterval);

    getHistory(
        function(status, response) {
            var x = updateHistory(status, response);
            if (x < 0) resetHistoryDaemonTimer();
        },
        function() {});
}

function runOnload() {
    var history = document.getElementById('history');
    history.appendChild(historyText);

    var form = document.getElementById('form');
    form.addEventListener('submit', function(event) {
        resetHistoryDaemonTimer();

        var codeElement = document.getElementById('code');
        var code = codeElement.value;
        codeElement.value = '';
        historyText.data += code + '\n';

        sendCode(code, function(s) {}, function() {});
        return false;
    });

    var clear = document.getElementById('clear');
    clear.addEventListener('click', function(event) {
        resetHistoryDaemonTimer();

        sendClear(function() {}, function() {});
        return false;
    });

    historyDaemonId = window.setTimeout(historyDaemon, historyDaemonInterval);
}
</script>
<style>
#code {
    font-family: monospace;
}
</style>
</head>
<body onload="runOnload()">
<pre>
<code id="history"></code>
</pre>
<form id="form" action="javascript:void(0);">
    <input type="text" id="code" name="code" value="">
    <input type="submit" value="Submit">
    <input type="button" id="clear" value="Clear output">
</form>
</body>
</html>'''
    return Handler


def start(globals, return_server=False):
    handler = make_handler(globals)
    server = _socketserver.TCPServer(('', 0), handler)
    port = server.socket.getsockname()[1]

    def serve(handler):
        with server:
            server.serve_forever()
    _threading.Thread(target=serve, args=(handler,), daemon=True).start()

    if return_server:
        return port, handler.root_path, server
    return port, handler.root_path
