/*
 * sched_yield que dorme: carregado so no jogo (LD_PRELOAD), 32 bits como o AoMX.exe.
 *
 * O limitador de quadros do AoM EE espera o proximo quadro em laco: consulta o
 * tempo de CPU (GetThreadTimes -> getrusage) e cede a vez (Sleep(0)/SwitchToThread
 * -> NtYieldExecution -> sched_yield), sem dormir. Medido em 2026-10-14 no menu, a
 * 30 fps: 213 mil getrusage/s e 107 mil sched_yield/s, thread principal a 98% e 55%
 * do tempo dela no kernel. sched_yield so cede se houver outra thread pronta; com
 * CPU sobrando volta na hora e o laco gira.
 *
 * Aqui cada sched_yield dorme AOM_YIELD_US microssegundos (sem a variavel, ou 0,
 * comporta-se como o original).
 *
 * So dormir nao basta: o laco mede a espera pelo tempo de CPU da propria thread
 * (2 getrusage por sched_yield). Dormindo, esse relogio anda devagar e o jogo
 * espera mais: medido 20 us -> 20 fps, 100 us -> 10 fps, 500 us -> 3 fps (era 30).
 * Com AOM_YIELD_US ligado, getrusage(RUSAGE_THREAD) devolve o tempo de relogio
 * (CLOCK_MONOTONIC) como tempo de usuario: o laco ve o tempo passar enquanto dorme.
 *
 * Build: gcc -m32 -O2 -shared -fPIC -o yieldsleep32.so yieldsleep.c
 */
#define _GNU_SOURCE
#include <stdlib.h>
#include <sys/syscall.h>
#include <time.h>
#include <sys/resource.h>
#include <unistd.h>

static long sleep_ns = -1;

static void init(void)
{
    if (sleep_ns < 0) {
        const char *e = getenv("AOM_YIELD_US");
        sleep_ns = e ? atol(e) * 1000L : 0;
    }
}

int getrusage(int who, struct rusage *ru)
{
    int r = (int)syscall(SYS_getrusage, who, ru);
    init();
    if (r == 0 && sleep_ns > 0 && (who == RUSAGE_THREAD || who == RUSAGE_SELF)) {
        struct timespec t;
        clock_gettime(CLOCK_MONOTONIC, &t);
        ru->ru_utime.tv_sec = t.tv_sec;
        ru->ru_utime.tv_usec = t.tv_nsec / 1000;
        ru->ru_stime.tv_sec = 0;
        ru->ru_stime.tv_usec = 0;
    }
    return r;
}

int sched_yield(void)
{
    init();
    if (sleep_ns > 0) {
        struct timespec t = { 0, sleep_ns };
        nanosleep(&t, NULL);
        return 0;
    }
    return (int)syscall(SYS_sched_yield);
}
