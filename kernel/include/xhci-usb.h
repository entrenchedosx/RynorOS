#ifndef RYNOR_XHCI_USB_H
#define RYNOR_XHCI_USB_H
#include "cpu.h"
#include "xhci.h"
#include "usb.h"

/* USB-A1 enumeration engine: root ports → slots → Address Device →
   EP0 control transfers. Uses the xHCI-A1 engine (rings, ISR, MSI-X);
   adds no second controller path. */

enum xusb_result {
    XUSB_OK = 0,
    XUSB_INVALID,
    XUSB_HW,
    XUSB_RANGE,
    XUSB_STATE,
    XUSB_TIMEOUT,
    XUSB_NOMEM,
    XUSB_FAILED_DEV,
    XUSB_UNSUPPORTED
};

/* Fault-injection stages (§61); production passes NONE. */
enum xusb_fail_at {
    XUSB_FAIL_NONE = 0,
    XUSB_FAIL_PROTO,
    XUSB_FAIL_SCAN,
    XUSB_FAIL_RESET,
    XUSB_FAIL_ENSLOT,
    XUSB_FAIL_DCTX,
    XUSB_FAIL_ICTX,
    XUSB_FAIL_EP0RING,
    XUSB_FAIL_ADDR,
    XUSB_FAIL_DESC8,
    XUSB_FAIL_EVAL,
    XUSB_FAIL_DESC18
};

/* Port state machine (explicit; no boolean soup). */
enum xusb_port_state {
    XUSB_DISCONNECTED = 0,
    XUSB_CONNECTED,
    XUSB_RESETTING,
    XUSB_ENABLED,
    XUSB_SLOT,
    XUSB_ADDRESSING,
    XUSB_ADDRESSED,
    XUSB_EP0READY,
    XUSB_DESC8,
    XUSB_DESCDONE,
    XUSB_FAILED
};

/* One Supported Protocol capability (ID 2). */
struct xusb_proto {
    cpu_u8 major, minor;
    cpu_u8 port_off;
    cpu_u8 port_count;
    cpu_u8 psi_count;
    cpu_u32 psi[XHCI_PSI_MAX];
    cpu_u64 cap_off;
};

/* One root port under USB management. */
struct xusb_port {
    cpu_u32 num;
    cpu_u8 proto;
    cpu_u8 state;
    cpu_u8 speed_id;
    cpu_u8 speed;
    cpu_u8 slot;
    cpu_u32 last_ps;
};

/* The USB bus: one controller, bounded protocols + ports. */
struct xusb_bus {
    struct xhci_hcd *hcd;
    struct xusb_proto protos[XHCI_PROTOS_MAX];
    cpu_u32 nprotos;
    struct xusb_port ports[XHCI_PORTS_MAX];
    cpu_u32 nports;
};

/* One device under enumeration (caller-owned storage). */
#define XUSB_EP0_TRBS 10u
struct xusb_dev {
    struct xusb_bus *bus;
    cpu_u32 port_idx;
    cpu_u8 slot;
    cpu_u8 addr;
    struct dma_buffer dctx;
    struct dma_buffer ictx;
    struct xhci_ring ep0;
    cpu_u16 ep0_mps;
    cpu_u32 addr_control;
};

enum xusb_result xusb_parse_protos(struct xusb_bus *bus);
enum xusb_result xusb_scan_ports(struct xusb_bus *bus);
enum usb_speed xusb_port_speed(const struct xusb_bus *bus, cpu_u32 idx);
enum xusb_result xusb_port_reset(struct xusb_bus *bus, cpu_u32 idx);
enum xusb_result xusb_port_ack(struct xusb_bus *bus, cpu_u32 idx,
                               cpu_u32 bits);
enum xusb_result xusb_enable_slot(struct xusb_dev *dev);
enum xusb_result xusb_disable_slot(struct xusb_dev *dev);
enum xusb_result xusb_alloc_contexts(struct xusb_dev *dev);
void xusb_free_contexts(struct xusb_dev *dev);
enum xusb_result xusb_ep0_ring_alloc(struct xusb_dev *dev);
void xusb_ep0_ring_free(struct xusb_dev *dev);
enum xusb_result xusb_address_device(struct xusb_dev *dev, int bsr);
enum xusb_result xusb_evaluate_ep0(struct xusb_dev *dev, cpu_u16 mps);
enum xusb_result xusb_control(struct xusb_dev *dev,
                              const struct usb_setup *setup,
                              cpu_u64 data_bus, cpu_u32 data_len, int dir_in);
enum xusb_result xusb_get_descriptor(struct xusb_dev *dev, cpu_u8 type,
                                     cpu_u8 index, cpu_u64 data_bus,
                                     cpu_u16 wlen);
void xusb_slot_ctx_init(cpu_u32 *s, cpu_u8 speed_id, cpu_u32 port);
void xusb_ep0_ctx_init(cpu_u32 *ep, cpu_u64 ring_bus, cpu_u16 mps);
enum xusb_result xusb_enumerate(struct xusb_dev *dev, cpu_u32 port_idx,
                                enum xusb_fail_at fail_at,
                                cpu_u8 desc_out[USB_DT_DEVICE_SIZE]);
void xusb_dev_cleanup(struct xusb_dev *dev);
cpu_u32 xusb_last_addr_control(const struct xusb_dev *dev);
void xusb_inject_force(enum xusb_fail_at f);

/* Gated self-test driver (usb_test images only). */
void usb_self_test(void);

#endif
