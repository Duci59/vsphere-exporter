from pyVmomi import vim
from pyVim.connect import SmartConnect, Disconnect
from prometheus_client import start_http_server, Gauge, REGISTRY
from prometheus_client import PROCESS_COLLECTOR, PLATFORM_COLLECTOR
import ssl, time, yaml, logging, os

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
log = logging.getLogger(__name__)

REGISTRY.unregister(PROCESS_COLLECTOR)
REGISTRY.unregister(PLATFORM_COLLECTOR)
try:
    from prometheus_client import GC_COLLECTOR
    REGISTRY.unregister(GC_COLLECTOR)
except ImportError:
    pass

LABELS_FOLDER = ['folder', 'datacenter', 'vcenter']
# Them 'uuid' va 'moid': day la 2 field ma exporter network (vmware_exporter/
# vsphere_exporter) da dung san lam dinh danh VM (uuid = vm.config.uuid,
# moid = vm._moId dang "vm-XXXXX"). Gan giong het de join giua 2 exporter
# bang on(uuid) thay vi on(vmname) -> tranh trung ten VM giua cac folder.
LABELS_VM     = ['vmname', 'folder', 'datacenter', 'vcenter', 'power_state',
                  'uuid', 'moid']
# Label cho metric muc vCenter (tong hop toan bo host/datastore trong 1
# vCenter) - dung de ve panel "Capacity and Usage" kieu vSphere Client.
LABELS_VC     = ['vcenter']

folder_vcpu_alloc  = Gauge('vsphere_folder_vcpu_allocated',
    'Total vCPU allocated in folder', LABELS_FOLDER)
folder_ram_alloc   = Gauge('vsphere_folder_ram_allocated_gb',
    'Total RAM allocated in folder (GB)', LABELS_FOLDER)
folder_ram_used    = Gauge('vsphere_folder_ram_used_gb',
    'Total RAM used in folder (GB)', LABELS_FOLDER)
folder_disk_alloc  = Gauge('vsphere_folder_disk_allocated_gb',
    'Total disk allocated in folder (GB)', LABELS_FOLDER)
folder_disk_used   = Gauge('vsphere_folder_disk_used_gb',
    'Total disk used in folder (GB)', LABELS_FOLDER)
folder_vm_total    = Gauge('vsphere_folder_vm_total',
    'Total VMs in folder', LABELS_FOLDER)
folder_vm_on       = Gauge('vsphere_folder_vm_powered_on',
    'Total powered-on VMs in folder', LABELS_FOLDER)

vm_vcpu       = Gauge('vsphere_vm_vcpu_allocated',
    'vCPU allocated per VM', LABELS_VM)
vm_cpu_mhz    = Gauge('vsphere_vm_cpu_usage_mhz',
    'CPU usage MHz per VM', LABELS_VM)
vm_cpu_cap_mhz = Gauge('vsphere_vm_cpu_capacity_mhz',
    'Max CPU capacity per VM in MHz (vm.runtime.maxCpuUsage — dung de tinh CPU %)', LABELS_VM)
vm_ram_alloc  = Gauge('vsphere_vm_ram_allocated_gb',
    'RAM allocated per VM (GB)', LABELS_VM)
vm_ram_used   = Gauge('vsphere_vm_ram_used_gb',
    'RAM used per VM (GB)', LABELS_VM)
vm_disk_alloc = Gauge('vsphere_vm_disk_allocated_gb',
    'Disk allocated per VM (GB)', LABELS_VM)
vm_disk_used  = Gauge('vsphere_vm_disk_used_gb',
    'Disk used per VM (GB)', LABELS_VM)

# --- Metric muc vCenter: khop voi panel "Capacity and Usage" cua vSphere
# Client (CPU GHz, Memory GB, Storage GB - used/free/capacity). Lay tu
# tong hop HOST (CPU/Memory - vi capacity vat ly nam o host, khong phai
# VM) va DATASTORE (Storage), khong phai cong don tu VM.
vc_cpu_capacity_mhz = Gauge('vsphere_vcenter_cpu_capacity_mhz',
    'Total CPU capacity across all hosts (MHz)', LABELS_VC)
vc_cpu_used_mhz     = Gauge('vsphere_vcenter_cpu_used_mhz',
    'Total CPU used across all hosts (MHz)', LABELS_VC)
vc_mem_capacity_gb  = Gauge('vsphere_vcenter_mem_capacity_gb',
    'Total memory capacity across all hosts (GB)', LABELS_VC)
vc_mem_used_gb      = Gauge('vsphere_vcenter_mem_used_gb',
    'Total memory used across all hosts (GB)', LABELS_VC)
vc_disk_capacity_gb = Gauge('vsphere_vcenter_disk_capacity_gb',
    'Total datastore capacity across vCenter (GB)', LABELS_VC)
vc_disk_used_gb     = Gauge('vsphere_vcenter_disk_used_gb',
    'Total datastore used across vCenter (GB)', LABELS_VC)
vc_host_total       = Gauge('vsphere_vcenter_host_total',
    'Total ESXi hosts in vCenter', LABELS_VC)
vc_host_connected   = Gauge('vsphere_vcenter_host_connected',
    'Connected ESXi hosts in vCenter', LABELS_VC)

collect_duration = Gauge('vsphere_collect_duration_seconds',
    'Collection duration per vCenter', ['vcenter'])
collect_success  = Gauge('vsphere_collect_success',
    'Collection success (1=ok, 0=fail)', ['vcenter'])

# Cac Gauge co label dong (vmname, power_state, ...) can duoc clear
# truoc moi chu ky collect, neu khong Prometheus se giu lai cac to hop
# label cu (vd power_state khac) da bien mat -> sinh ra series "duplicate".
DYNAMIC_GAUGES = [
    folder_vcpu_alloc, folder_ram_alloc, folder_ram_used,
    folder_disk_alloc, folder_disk_used, folder_vm_total, folder_vm_on,
    vm_vcpu, vm_cpu_mhz, vm_cpu_cap_mhz, vm_ram_alloc, vm_ram_used,
    vm_disk_alloc, vm_disk_used,
]

def clear_dynamic_metrics():
    for g in DYNAMIC_GAUGES:
        g.clear()

def get_folder_path(vm):
    path = []
    obj  = vm.parent
    while obj and not isinstance(obj, vim.Datacenter):
        if isinstance(obj, vim.Folder) and obj.name != 'vm':
            path.insert(0, obj.name)
        obj = obj.parent
    return '/'.join(path) if path else '/'

def get_datacenter(vm):
    obj = vm.parent
    while obj:
        if isinstance(obj, vim.Datacenter):
            return obj.name
        obj = getattr(obj, 'parent', None)
    return 'unknown'

def connect(cfg):
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    if cfg.get('ignore_ssl', True):
        ctx.verify_mode = ssl.CERT_NONE
    return SmartConnect(host=cfg['host'],
        user=cfg['username'], pwd=cfg['password'],
        sslContext=ctx)

def collect_vcenter_summary(content, host):
    """
    Tong hop CPU/Memory tu tat ca HOST va Storage tu tat ca DATASTORE
    trong 1 vCenter. Day chinh la so lieu hien thi o panel "Capacity and
    Usage" tren vSphere Client (khac voi cong don tu VM, vi VM khong
    phan anh dung capacity vat ly cua ha tang).
    """
    # --- CPU + Memory: cong don tu host ---
    host_view = content.viewManager.CreateContainerView(
        content.rootFolder, [vim.HostSystem], True)
    try:
        cpu_cap_mhz = 0
        cpu_used_mhz = 0
        mem_cap_mb = 0
        mem_used_mb = 0
        total_hosts = 0
        connected_hosts = 0
        for h in host_view.view:
            total_hosts += 1
            try:
                if h.runtime.connectionState != vim.HostSystem.ConnectionState.connected:
                    continue
                connected_hosts += 1
                hw  = h.summary.hardware
                qs  = h.summary.quickStats
                # capacity CPU (MHz) = so lg core * tan so CPU (MHz/core)
                cpu_cap_mhz  += hw.numCpuCores * hw.cpuMhz
                cpu_used_mhz += qs.overallCpuUsage or 0
                mem_cap_mb   += hw.memorySize / 1048576  # bytes -> MB
                mem_used_mb  += qs.overallMemoryUsage or 0  # da la MB
            except Exception as e:
                log.warning(f'[{host}] Skip host in vcenter summary: {e}')
    finally:
        host_view.Destroy()

    # --- Storage: cong don tu datastore, dedupe theo _moId vi 1
    # datastore co the duoc nhieu host/cluster share ---
    ds_view = content.viewManager.CreateContainerView(
        content.rootFolder, [vim.Datastore], True)
    try:
        disk_cap_gb = 0.0
        disk_used_gb = 0.0
        seen = set()
        for ds in ds_view.view:
            moid = getattr(ds, '_moId', None)
            if moid is None or moid in seen:
                continue
            seen.add(moid)
            try:
                s = ds.summary
                if not s.accessible:
                    continue
                cap_gb  = s.capacity / 1073741824
                free_gb = s.freeSpace / 1073741824
                disk_cap_gb  += cap_gb
                disk_used_gb += (cap_gb - free_gb)
            except Exception as e:
                log.warning(f'[{host}] Skip datastore in vcenter summary: {e}')
    finally:
        ds_view.Destroy()

    vc_cpu_capacity_mhz.labels(vcenter=host).set(cpu_cap_mhz)
    vc_cpu_used_mhz.labels(vcenter=host).set(cpu_used_mhz)
    vc_mem_capacity_gb.labels(vcenter=host).set(round(mem_cap_mb/1024, 2))
    vc_mem_used_gb.labels(vcenter=host).set(round(mem_used_mb/1024, 2))
    vc_disk_capacity_gb.labels(vcenter=host).set(round(disk_cap_gb, 2))
    vc_disk_used_gb.labels(vcenter=host).set(round(disk_used_gb, 2))
    vc_host_total.labels(vcenter=host).set(total_hosts)
    vc_host_connected.labels(vcenter=host).set(connected_hosts)

    log.info(f'[{host}] Summary — CPU {cpu_used_mhz}/{cpu_cap_mhz} MHz, '
             f'RAM {round(mem_used_mb/1024,1)}/{round(mem_cap_mb/1024,1)} GB, '
             f'Disk {round(disk_used_gb,1)}/{round(disk_cap_gb,1)} GB, '
             f'Hosts {connected_hosts}/{total_hosts}')

def collect_vcenter(cfg):
    host = cfg['host']
    t0   = time.time()
    try:
        si = connect(cfg)
    except Exception as e:
        log.error(f'[{host}] Connection failed: {e}')
        collect_success.labels(vcenter=host).set(0)
        return
    container = None
    try:
        content   = si.RetrieveContent()

        # Tong hop CPU/Memory/Storage muc vCenter (panel "Capacity and Usage")
        try:
            collect_vcenter_summary(content, host)
        except Exception as e:
            log.error(f'[{host}] vCenter summary collection failed: {e}')

        container = content.viewManager.CreateContainerView(
            content.rootFolder, [vim.VirtualMachine], True)
        folder_stats = {}
        for vm in container.view:
            # Dung _moId (luu san o client, khong can goi RPC) de log khi
            # loi xay ra, tranh truong hop VM da bi xoa: goi vm.name trong
            # khoi except cung se RPC va nem loi lan 2 -> thoat ca vong for,
            # mat toan bo du liệu da thu thap cua vCenter nay trong chu ky.
            moid = getattr(vm, '_moId', 'unknown')
            try:
                folder = get_folder_path(vm)
                dc     = get_datacenter(vm)
                state  = vm.runtime.powerState
                # uuid: dung config.uuid (BIOS UUID) - day la cung 1 field
                # ma exporter network (vmware_exporter) dung lam label
                # 'uuid'. Giup join 2 metric family bang on(uuid) chinh xac
                # tuyet doi, khong so trung ten VM giua cac folder khac nhau.
                vm_uuid = vm.config.uuid if vm.config else 'unknown'
                vcpu       = vm.config.hardware.numCPU
                ram_alloc  = round(vm.config.hardware.memoryMB/1024, 3)
                # disk_alloc = committed + uncommitted (tong provisioned)
                disk_alloc = round(
                    (vm.summary.storage.committed +
                     vm.summary.storage.uncommitted)/1073741824, 3)
                cpu_mhz   = vm.summary.quickStats.overallCpuUsage or 0
                # maxCpuUsage: cong suat CPU toi da (MHz) VM duoc cap, do
                # vCenter tinh tu tan so CPU that cua host + so vCPU + limit.
                # Dung lam mau so de tinh CPU % dung, thay vi doan MHz/core.
                cpu_cap_mhz = vm.runtime.maxCpuUsage or 0
                ram_used  = round(
                    (vm.summary.quickStats.guestMemoryUsage or 0)/1024, 3)
                # disk_used = committed = byte da ghi thuc te (luon duong)
                disk_used = round(
                    vm.summary.storage.committed/1073741824, 3)
                vml = dict(vmname=vm.name, folder=folder,
                    datacenter=dc, vcenter=host, power_state=state,
                    uuid=vm_uuid, moid=moid)
                vm_vcpu.labels(**vml).set(vcpu)
                vm_cpu_mhz.labels(**vml).set(cpu_mhz)
                vm_cpu_cap_mhz.labels(**vml).set(cpu_cap_mhz)
                vm_ram_alloc.labels(**vml).set(ram_alloc)
                vm_ram_used.labels(**vml).set(ram_used)
                vm_disk_alloc.labels(**vml).set(disk_alloc)
                vm_disk_used.labels(**vml).set(disk_used)
                key = (folder, dc)
                if key not in folder_stats:
                    folder_stats[key] = dict(
                        vcpu=0, ram_alloc=0.0, ram_used=0.0,
                        disk_alloc=0.0, disk_used=0.0,
                        total=0, on=0)
                s = folder_stats[key]
                s['vcpu']       += vcpu
                s['ram_alloc']  += ram_alloc
                s['ram_used']   += ram_used
                s['disk_alloc'] += disk_alloc
                s['disk_used']  += disk_used
                s['total']      += 1
                if state == 'poweredOn': s['on'] += 1
            except Exception as e:
                log.warning(f'[{host}] Skip VM ({moid}): {e}')
        for (folder, dc), s in folder_stats.items():
            fl = dict(folder=folder, datacenter=dc, vcenter=host)
            folder_vcpu_alloc.labels(**fl).set(s['vcpu'])
            folder_ram_alloc.labels(**fl).set(round(s['ram_alloc'],2))
            folder_ram_used.labels(**fl).set(round(s['ram_used'],2))
            folder_disk_alloc.labels(**fl).set(round(s['disk_alloc'],2))
            folder_disk_used.labels(**fl).set(round(s['disk_used'],2))
            folder_vm_total.labels(**fl).set(s['total'])
            folder_vm_on.labels(**fl).set(s['on'])
        elapsed = round(time.time() - t0, 2)
        collect_success.labels(vcenter=host).set(1)
        collect_duration.labels(vcenter=host).set(elapsed)
        log.info(f'[{host}] OK — {sum(s["total"] for s in folder_stats.values())} VMs, {elapsed}s')
    except Exception as e:
        log.error(f'[{host}] Error: {e}')
        collect_success.labels(vcenter=host).set(0)
    finally:
        if container is not None:
            try: container.Destroy()
            except Exception as e:
                log.warning(f'[{host}] Could not destroy container view: {e}')
        try: Disconnect(si)
        except: pass

def load_config(path):
    with open(path) as f: return yaml.safe_load(f)

if __name__ == '__main__':
    cfg_path = os.environ.get(
        'CONFIG_PATH', '/opt/vsphere-exporter/config.yml')
    cfg      = load_config(cfg_path)
    port     = cfg.get('exporter_port', 9100)
    interval = cfg.get('collect_interval', 300)
    vcenters = cfg.get('vcenters', [])
    if not vcenters:
        log.error('No vcenters defined in config.yml'); exit(1)
    log.info(f'Starting vsphere-folder-exporter on :{port}')
    log.info(f'Monitoring {len(vcenters)} vCenter(s)')
    start_http_server(port)
    while True:
        log.info('Starting collection cycle...')
        # Xoa toan bo series cu (vmname/power_state/folder...) truoc khi
        # collect lai, tranh giu lai to hop label da khong con dung nua.
        clear_dynamic_metrics()
        for vc in vcenters: collect_vcenter(vc)
        log.info(f'Done. Sleeping {interval}s...')
        time.sleep(interval)