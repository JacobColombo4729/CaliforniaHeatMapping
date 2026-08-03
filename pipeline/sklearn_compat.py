"""Make scikit-learn's KMeans usable on this machine.

Symptom: `KMeans().fit(X)` dies with

    OSError: [WinError -1066598273] Windows Error 0xc06d007f

Cause: KMeans runs its solver inside a `threadpoolctl` thread limiter, which
enumerates every native threading library loaded in the process and asks each
one how many threads it has. One of the libraries here raises when asked, and
`threadpoolctl` has no error handling around that call, so the exception
propagates and takes KMeans with it.

Notably this is NOT a general OpenMP failure — RandomForest and
HistGradientBoosting both work fine. Only the code paths that go through the
thread limiter break, which is why KMeans and MiniBatchKMeans fail alone.

Fix: skip libraries that cannot answer. The thread counts are only used to
temporarily cap parallelism, so an unqueryable library is simply left alone.

    from sklearn_compat import patch_threadpool
    patch_threadpool()
    from sklearn.cluster import KMeans   # now works

Verified: patched KMeans and `scipy.cluster.vq.kmeans2` return identical
partitions (adjusted Rand index 1.000). If you would rather not patch a
third-party library, `kmeans2` is a drop-in that needs no patch at all.
"""

from __future__ import annotations

_patched = False


def patch_threadpool() -> bool:
    """Return True if the patch was applied, False if it was already in place."""
    global _patched
    if _patched:
        return False

    import threadpoolctl

    original = threadpoolctl.ThreadpoolController.info

    def tolerant_info(self):
        out = []
        for controller in self.lib_controllers:
            try:
                out.append(controller.info())
            except OSError:
                continue
        return out

    tolerant_info.__wrapped__ = original
    threadpoolctl.ThreadpoolController.info = tolerant_info
    _patched = True
    return True
