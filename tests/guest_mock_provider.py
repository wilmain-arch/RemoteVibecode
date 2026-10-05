"""Loopback Responses API fixture. Never connects to a model provider."""
import json
import threading
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer

class MockProvider:
    def __init__(self, command, tool_item=None):
        self.command=command
        self.tool_item=tool_item
        self.requests=[]
        parent=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_POST(self):
                body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                parent.requests.append(body)
                count=len(parent.requests)
                if count>2:
                    self.send_response(500);self.end_headers();return
                if count==1:
                    item=parent.tool_item or {'id':'fc_fixture','type':'function_call','call_id':'call_fixture','name':'exec_command',
                          'arguments':json.dumps({'cmd':parent.command,'yield_time_ms':1000,'max_output_tokens':1000})}
                else:
                    item={'id':'msg_fixture','type':'message','role':'assistant','content':[{'type':'output_text','text':'Synthetic completed','annotations':[]}]}
                rid='resp_fixture_'+str(count)
                events=[{'type':'response.created','response':{'id':rid}},
                        {'type':'response.output_item.added','output_index':0,'item':item},
                        {'type':'response.output_item.done','output_index':0,'item':item},
                        {'type':'response.completed','response':{'id':rid,'status':'completed','output':[item],
                            'usage':{'input_tokens':10,'output_tokens':5,'total_tokens':15}}}]
                encoded=''.join('data: '+json.dumps(e)+'\n\n' for e in events).encode()
                self.send_response(200);self.send_header('Content-Type','text/event-stream');self.send_header('Content-Length',str(len(encoded)));self.end_headers();self.wfile.write(encoded)
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.url=f'http://127.0.0.1:{self.server.server_port}/v1'
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
    def close(self):self.server.shutdown();self.server.server_close();self.thread.join(timeout=2)
