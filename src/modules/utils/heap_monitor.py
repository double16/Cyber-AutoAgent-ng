import threading, time, os, signal
import psutil, tracemalloc, faulthandler

tracemalloc.start()

def take_dumps(prefix='/var/tmp/heap'):
    # tracemalloc snapshot
    print('Taking heap snapshot...')
    s = tracemalloc.take_snapshot()
    s.dump(f'{prefix}_tracemalloc.snapshot')
    # faulthandler traceback
    print('Taking traceback...')
    with open(f'{prefix}_traceback.txt','w') as f:
        faulthandler.dump_traceback(file=f)

    # optionally trigger core (dangerous)
    # os.kill(os.getpid(), signal.SIGABRT)

def monitor(interval=3.0, threshold_ratio=0.80):
    p = psutil.Process()
    # determine limit (cgroup)
    limit = None
    for path in ('/sys/fs/cgroup/memory/memory.limit_in_bytes',
                 '/sys/fs/cgroup/memory.max'):
        try:
            with open(path) as fh:
                v = fh.read().strip()
            if v and v != 'max':
                limit = int(v)
                break
        except Exception:
            pass
    if not limit:
        import resource
        limit, _ = resource.getrlimit(resource.RLIMIT_AS)
        if limit == resource.RLIM_INFINITY:
            limit = None

    if not limit:
        print("WARNING: unable to determine memory limit, heap monitoring disabled")
        return

    while True:
        mem = p.memory_info().rss
        # print(f"Heap usage: {mem} bytes / {limit} bytes {mem / limit:.2%}")
        if mem / limit > threshold_ratio:
            take_dumps()
            # back off and keep sampling less frequently to avoid spamming
            time.sleep(30)
        time.sleep(interval)

t = threading.Thread(target=monitor, daemon=True)
t.start()

if __name__ == '__main__':
    take_dumps()
