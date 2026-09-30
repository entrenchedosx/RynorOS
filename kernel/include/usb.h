#ifndef RYNOR_USB_H
#define RYNOR_USB_H
#include "cpu.h"

/* USB-A1 core: USB 2.0 chapter-9 truth (Linux ch9.h) + descriptor
   parsing. Pure byte-level code; no xHCI, no DMA, no hardware. */

enum usb_result {
    USB_OK = 0,
    USB_INVALID,
    USB_SHORT,
    USB_MISMATCH,
    USB_UNSUPPORTED
};

/* USB speeds (canonical internal enum; ch9 usb_device_speed parity). */
enum usb_speed {
    USB_SPEED_UNKNOWN = 0,
    USB_SPEED_LOW,
    USB_SPEED_FULL,
    USB_SPEED_HIGH,
    USB_SPEED_SUPER,
    USB_SPEED_SUPERPLUS
};

/* Standard request codes (ch9). */
#define USB_REQ_GET_STATUS        0x00u
#define USB_REQ_CLEAR_FEATURE     0x01u
#define USB_REQ_SET_FEATURE       0x03u
#define USB_REQ_SET_ADDRESS       0x05u
#define USB_REQ_GET_DESCRIPTOR    0x06u
#define USB_REQ_SET_DESCRIPTOR    0x07u
#define USB_REQ_GET_CONFIGURATION 0x08u
#define USB_REQ_SET_CONFIGURATION 0x09u

/* bmRequestType (ch9). */
#define USB_DIR_OUT 0x00u
#define USB_DIR_IN  0x80u
#define USB_TYPE_STANDARD 0x00u
#define USB_RECIP_DEVICE  0x00u

/* Descriptor types (ch9). */
#define USB_DT_DEVICE    0x01u
#define USB_DT_CONFIG    0x02u
#define USB_DT_DEVICE_SIZE 18u

/* An 8-byte setup packet: explicit bytes, never padded-struct
   dependent. */
struct usb_setup {
    cpu_u8 bytes[8];
};

void usb_setup_get_desc(struct usb_setup *s, cpu_u8 type, cpu_u8 index,
                        cpu_u16 wlen);

/* Parsed device descriptor (all multi-byte fields LE-decoded). */
struct usb_device_desc {
    cpu_u8 bLength;
    cpu_u8 bDescriptorType;
    cpu_u16 bcdUSB;
    cpu_u8 bDeviceClass;
    cpu_u8 bDeviceSubClass;
    cpu_u8 bDeviceProtocol;
    cpu_u8 bMaxPacketSize0;
    cpu_u16 idVendor;
    cpu_u16 idProduct;
    cpu_u16 bcdDevice;
    cpu_u8 iManufacturer;
    cpu_u8 iProduct;
    cpu_u8 iSerialNumber;
    cpu_u8 bNumConfigurations;
};

enum usb_result usb_parse_device_desc(const cpu_u8 *bytes, cpu_u32 len,
                                      struct usb_device_desc *out);

/* Initial EP0 max-packet-size for a speed when the descriptor is not
   yet known (USB 2.0 §5.5.3 / §9.6.1 semantics): HS 64 and SS 512
   are fixed; FS/LS start at 8 and learn the real value from the
   first descriptor bytes. Returns 0 when the speed is unusable. */
cpu_u16 usb_ep0_initial_mps(enum usb_speed speed);

/* Whether an EP0 context update is required after learning the real
   bMaxPacketSize0: true only for FS/LS with initial != actual. For
   fixed-MPS speeds (HS/SS) a mismatch is device malformation, which
   the caller must fail rather than update. */
int usb_ep0_mps_needs_update(enum usb_speed speed, cpu_u8 initial,
                             cpu_u8 actual);

#endif
