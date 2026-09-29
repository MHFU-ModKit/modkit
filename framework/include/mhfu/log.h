/* Framework log, ms0:/PSP/PLUGINS/mhfu_framework/framework.log. Lines are held in memory
 * until ms0 I/O is safe, so a mod that fails at boot still shows up. */
#ifndef MHFU_LOG_H
#define MHFU_LOG_H

#ifdef __cplusplus
extern "C" {
#endif

void mhfu_log(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
void mhfu_log_close(void);

#ifdef __cplusplus
} /* extern "C" */
#endif

#endif /* MHFU_LOG_H */
