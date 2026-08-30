"""Strict validation of real VM serial diagnostics, never an execution substitute."""
import re

VM_END = b"[TEST] VM self-test passed\r\n"


def parse_vm_output(output: bytes, pmm: dict | None = None) -> dict:
    if len(output) > 12000:
        raise ValueError("VM output too large")
    lines = iter(output.decode("ascii").splitlines(keepends=True))
    values = {}

    def exact(text):
        if next(lines, None) != text + "\r\n":
            raise ValueError("VM missing/out-of-order line: " + text)

    def numeric(pattern):
        match = re.fullmatch(pattern + r"\r\n", next(lines, ""))
        if not match:
            raise ValueError("VM invalid numeric record: " + pattern)
        result = [int(n) for n in match.groups()]
        if any(n >= 1 << 64 for n in result):
            raise ValueError("VM value outside uint64")
        return result

    exact("[VM] paging subsystem initialized")
    exact("[VM] kernel address space created")
    exact("[VM] CR3 loaded")
    root, tables = numeric(r"\[VM\] root=(\d+) table_pages=(\d+)")
    exact("[VM] kernel mappings verified")
    exact("[TEST] VM self-test started")
    va, physical, offset = numeric(r"\[VM\] mapping va=(\d+) physical=(\d+) offset_physical=(\d+)")
    if tables != 7 or va != 0x40000000 or physical % 4096 or root % 4096 or root == physical or offset != physical + 4088:
        raise ValueError("VM invalid root/mapping/translation")
    exact("[TEST] VM mapping verified")
    exact("[TEST] VM invalid mappings rejected")
    faults = []

    def fault(marker, error):
        exact(marker)
        exact("[VM] page fault")
        match = re.fullmatch(r"\[VM\] fault_address=0x([0-9a-f]{16}) error=0x([0-9a-f]{16}) rip=0x([0-9a-f]{16})\r\n", next(lines, ""))
        if not match:
            raise ValueError("VM missing hardware fault state")
        address, actual, rip = (int(n, 16) for n in match.groups())
        if address != va or actual != error or (rip != va if error == 17 else not 0x8000 <= rip < 0x70000):
            raise ValueError("VM fault address/error/RIP mismatch")
        exact(f"[VM] present={error & 1} write={(error >> 1) & 1} user=0 reserved=0 fetch={(error >> 4) & 1} cpl=0")
        exact("[VM] page fault action=resume_test")
        faults.append((address, actual, rip))

    fault("[TEST] triggering read-only page fault", 3)
    fault("[TEST] triggering non-executable page fault", 17)
    exact("[TEST] VM permissions verified")
    exact("[TEST] VM unmapping verified")
    fault("[TEST] triggering controlled page fault", 0)
    for marker in ("controlled page fault verified", "page fault diagnostics verified", "VM TLB invalidation verified",
                   "VM ranges and high addresses verified", "VM address-space destruction verified", "VM real OOM rollback verified"):
        exact("[TEST] " + marker)
    final_tables, allocated, free = numeric(r"\[VM\] final table_pages=(\d+) allocated_bytes=(\d+) free_bytes=(\d+)")
    exact("[TEST] VM self-test passed")
    if next(lines, None) is not None or final_tables != tables or allocated != tables * 4096 or free % 4096 or not free:
        raise ValueError("VM final accounting/trailing output invalid")
    if pmm:
        if allocated + free != pmm["free_bytes"]:
            raise ValueError("VM leaked frames or disagrees with PMM")
        for address in (root, physical):
            if not any(kind == 1 and a <= address < b for a, b, kind in pmm["regions"]):
                raise ValueError("VM frame outside discovered usable PMM memory")
    values.update(root=root, tables=tables, physical=physical, faults=faults, allocated=allocated, free=free)
    return values


def validate_vm_output(output: bytes, pmm: dict | None = None) -> list[str]:
    try:
        parse_vm_output(output, pmm)
        return []
    except (ValueError, UnicodeDecodeError) as error:
        return [str(error)]
