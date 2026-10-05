"""Native remote-environment runner; credentials stay in a private broker.

Not enabled in production until host idle detection and all tool routing have
been verified. The caller provisions the broker; this module never copies auth.
"""
import queue
import time
from .guest_executor_gateway import ExecutorGateway


class GuestRunner:
    def __init__(self, *, workspace, broker, codex, rpc_factory, on_started=None, on_progress=None, timeout=3600, resource_limits=False):
        self.workspace=workspace
        self.broker=broker
        self.codex=codex
        self.rpc_factory=rpc_factory
        self.on_started=on_started or (lambda task,thread,turn:None)
        self.on_progress=on_progress or (lambda task,messages:None)
        self.timeout=timeout
        self.resource_limits=resource_limits
        self.gateway=None
        self.rpc=None

    def __call__(self, task, checkpoint):
        gateway=ExecutorGateway(self.workspace,self.codex,resource_limits=self.resource_limits)
        self.gateway=gateway
        rpc=None
        try:
            flags=[]
            for feature in ('apps','hooks','plugins','remote_plugin','multi_agent'):
                flags.extend(['-c',f'features.{feature}=false'])
            flags.extend(['-c','web_search="disabled"'])
            rpc=self.rpc_factory(executable_override=str(self.codex),
                process_env={'HOME':str(self.broker),'CODEX_HOME':str(self.broker),'PATH':'/usr/bin'},
                process_cwd=str(self.broker),config_args=flags)
            self.rpc=rpc
            env_id='rv-guest-isolated'
            environments=[{'environmentId':env_id,'cwd':'/workspace','runtimeWorkspaceRoots':['/workspace']}]
            rpc.call('environment/add',{'environmentId':env_id,'execServerUrl':gateway.url,
                'authBearerToken':gateway.token,'connectTimeoutMs':3000})
            project=None
            if task.get('guestId'):
                project=rpc.call('project/create',{'idempotencyKey':'guest-'+task['guestId']+'-'+task.get('scopeId','default'), 'name':'Гостевой проект', 'roots':[{'path':'/workspace'}]})['project']
            params={'cwd':'/workspace','approvalPolicy':'never','sandbox':'workspace-write','environments':environments}
            if task.get('threadId'):
                params['threadId']=task['threadId']
                thread=rpc.call('thread/resume',params)['thread']
            else:
                if project:params['projectId']=project['id']
                thread=rpc.call('thread/start',params)['thread']
            turn_params={'threadId':thread['id'],'clientUserMessageId':task['operationId'],
                'input':[{'type':'text','text':task['text'],'text_elements':[]}],
                'approvalPolicy':'never','sandboxPolicy':{'type':'workspaceWrite','writableRoots':['/workspace'],'networkAccess':False},
                'environments':environments}
            for key in ('model','effort'):
                if task.get(key):turn_params[key]=task[key]
            turn=rpc.call('turn/start',turn_params)['turn']
            self.on_started(task,thread['id'],turn['id'])
            deadline=time.monotonic()+self.timeout
            interrupt_deadline=None
            interrupt_sent=False
            messages={}
            next_check=0.0
            next_publish=0.0
            dirty=False
            stop_reason=''
            while time.monotonic()<deadline:
                stop=False
                if time.monotonic()>=next_check:
                    checked=checkpoint();stop=checked['stop'];next_check=time.monotonic()+2
                    if stop:stop_reason=checked.get('stopReason','')
                if stop and interrupt_deadline is None:
                    interrupt_deadline=time.monotonic()+10
                if interrupt_deadline and not interrupt_sent:
                    from .server import RpcError
                    try:
                        rpc.call('turn/interrupt',{'threadId':thread['id'],'turnId':turn['id']})
                        interrupt_sent=True
                    except RpcError:
                        # turn/start may reply before the turn becomes active.
                        # Keep consuming authoritative completion events and retry
                        # this idempotent interrupt until the bounded deadline.
                        pass
                if interrupt_deadline and time.monotonic()>interrupt_deadline:
                    raise TimeoutError('Не подтверждена остановка гостевой задачи')
                try:event=rpc.events.get(timeout=1)
                except queue.Empty:
                    if not rpc.healthy:raise RuntimeError('Гостевой процесс Codex остановлен')
                    continue
                params=event.get('params') or {}
                if params.get('threadId')!=thread['id']:continue
                if params.get('turnId') and params['turnId']!=turn['id']:continue
                if event.get('method')=='item/agentMessage/delta':
                    item_id=params.get('itemId')
                    if item_id:
                        messages[item_id]=(messages.get(item_id,'')+params.get('delta',''))[:1024*1024]
                        dirty=True
                if event.get('method')=='item/completed':
                    item=params.get('item') or {}
                    if item.get('type')=='agentMessage':
                        messages[item['id']]=item.get('text','')[:1024*1024]
                        dirty=True
                if dirty and time.monotonic()>=next_publish:
                    self.on_progress(task,list(messages.values()))
                    next_publish=time.monotonic()+0.5
                    dirty=False
                if event.get('method')=='turn/completed' and (params.get('turn') or {}).get('id')==turn['id']:
                    return {'threadId':thread['id'],'turnId':turn['id'],'status':params['turn']['status'],
                        'messages':list(messages.values()),'stopReason':stop_reason}
            raise TimeoutError('Гостевая задача превысила время ожидания')
        finally:
            if rpc:rpc.close()
            gateway.close()

    def abort(self):
        # Closing the executor destroys its PID namespace and child processes.
        if self.gateway:self.gateway.close()
        if self.rpc:self.rpc.close()
