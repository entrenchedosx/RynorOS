#ifndef RYNOR_SHD_H
#define RYNOR_SHD_H

/* Stage 18d Slice E shell boot driver (test images + shell images).
 * Runs last when the image is built with RYNOR_SHELL_BOOT=1; with 0 it
 * is never called and prints nothing, so default transcripts stay
 * byte-identical. Mounts a filesystem, loads /bin/sh, attaches it to
 * the bootstrap thread (stdin KBD, stdout SERIAL by default routing),
 * and enters it. Never returns: shell exit (or missing/malformed
 * image) ends in an explicit halt marker plus kernel halt. This file
 * loads and enters only; there is no shell evaluation in ring 0.
 * See kernel/core/shd.c. */
#ifndef RYNOR_SHELL_BOOT
#define RYNOR_SHELL_BOOT 0
#endif
/* Optional argv[0] script path for the shell (empty = interactive).
 * Supplied by the build (-DSHELL_SCRIPT_PATH); the driver passes it
 * through user_prepare_argv, never interprets it. */
#ifndef SHELL_SCRIPT_PATH
#define SHELL_SCRIPT_PATH ""
#endif
void shd_boot(void);

#endif
