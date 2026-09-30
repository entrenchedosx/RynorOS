#include "usb.h"

void usb_setup_get_desc(struct usb_setup *s, cpu_u8 type, cpu_u8 index,
                        cpu_u16 wlen)
{
    s->bytes[0] = (cpu_u8)(USB_DIR_IN | USB_TYPE_STANDARD | USB_RECIP_DEVICE);
    s->bytes[1] = USB_REQ_GET_DESCRIPTOR;
    s->bytes[2] = index;
    s->bytes[3] = type;
    s->bytes[4] = 0;
    s->bytes[5] = 0;
    s->bytes[6] = (cpu_u8)wlen;
    s->bytes[7] = (cpu_u8)(wlen >> 8);
}

enum usb_result usb_parse_device_desc(const cpu_u8 *bytes, cpu_u32 len,
                                      struct usb_device_desc *out)
{
    if (!bytes || !out)
        return USB_INVALID;
    if (len < USB_DT_DEVICE_SIZE)
        return USB_SHORT;
    if (bytes[0] < USB_DT_DEVICE_SIZE)
        return USB_MISMATCH;
    if (bytes[1] != USB_DT_DEVICE)
        return USB_MISMATCH;
    out->bLength = bytes[0];
    out->bDescriptorType = bytes[1];
    out->bcdUSB = (cpu_u16)bytes[2] | ((cpu_u16)bytes[3] << 8);
    out->bDeviceClass = bytes[4];
    out->bDeviceSubClass = bytes[5];
    out->bDeviceProtocol = bytes[6];
    out->bMaxPacketSize0 = bytes[7];
    out->idVendor = (cpu_u16)bytes[8] | ((cpu_u16)bytes[9] << 8);
    out->idProduct = (cpu_u16)bytes[10] | ((cpu_u16)bytes[11] << 8);
    out->bcdDevice = (cpu_u16)bytes[12] | ((cpu_u16)bytes[13] << 8);
    out->iManufacturer = bytes[14];
    out->iProduct = bytes[15];
    out->iSerialNumber = bytes[16];
    out->bNumConfigurations = bytes[17];
    /* bLength may exceed 18 on future devices; the first 18 bytes are
       still the device descriptor. Zero configurations is forbidden
       (a device must have at least one). Class/Vendor/Product accept
       anything (class 0 and vendor-specific are valid). */
    if (!out->bNumConfigurations)
        return USB_MISMATCH;
    return USB_OK;
}

cpu_u16 usb_ep0_initial_mps(enum usb_speed speed)
{
    switch (speed) {
    case USB_SPEED_HIGH:
        return 64;
    case USB_SPEED_FULL:
    case USB_SPEED_LOW:
        return 8;
    case USB_SPEED_SUPER:
    case USB_SPEED_SUPERPLUS:
        return 512;
    default:
        return 0;
    }
}

int usb_ep0_mps_needs_update(enum usb_speed speed, cpu_u8 initial,
                             cpu_u8 actual)
{
    /* Only FS/LS learn MPS0 from the descriptor (8/16/32/64). HS/SS
       values are architecturally fixed: a mismatch there is a
       malformed device, not an update — the caller fails it. */
    if (speed != USB_SPEED_FULL && speed != USB_SPEED_LOW)
        return 0;
    return initial != actual;
}
