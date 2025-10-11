#!/usr/bin/env python3
"""
Debug Tracer System - Detailliertes Function und Step Tracking
===============================================================

Dieses System dokumentiert jeden Funktionsaufruf, jeden Schritt und jeden Fehler
mit vollständigem Kontext für maximale Debugging-Transparenz.

Features:
- Function Entry/Exit Tracking mit Argumenten
- Step-by-Step Execution Logging
- Automatic Error Context Collection
- Call Stack Capture bei Fehlern
- Variable State Snapshots
- Execution Timeline mit Timing
"""

import time
import logging
import threading
import traceback
import inspect
import sys
from typing import Any, Dict, List, Optional, Callable
from dataclasses import dataclass, field
from functools import wraps
from datetime import datetime, timezone
import json

logger = logging.getLogger(__name__)

# Thread-local storage for tracking context
_context = threading.local()

@dataclass
class ExecutionStep:
    """Einzelner Ausführungsschritt"""
    timestamp: float
    function_name: str
    step_name: str
    step_type: str  # 'entry', 'step', 'exit', 'error'
    details: Dict[str, Any] = field(default_factory=dict)
    variables: Dict[str, Any] = field(default_factory=dict)
    duration_ms: Optional[float] = None

@dataclass
class ExecutionContext:
    """Kontext einer Funktionsausführung"""
    function_name: str
    thread_id: str
    start_time: float
    steps: List[ExecutionStep] = field(default_factory=list)
    arguments: Dict[str, Any] = field(default_factory=dict)
    result: Any = None
    error: Optional[Exception] = None
    call_stack: List[str] = field(default_factory=list)

    @property
    def duration_ms(self) -> float:
        if self.steps:
            return (self.steps[-1].timestamp - self.start_time) * 1000
        return 0.0

class DebugTracer:
    """Hauptklasse für Debug-Tracing"""

    def __init__(self,
                 log_level: int = logging.DEBUG,
                 max_contexts: int = 1000,
                 include_variables: bool = True,
                 include_arguments: bool = True):
        self.log_level = log_level
        self.max_contexts = max_contexts
        self.include_variables = include_variables
        self.include_arguments = include_arguments

        # Thread-safe storage
        self._lock = threading.RLock()
        self._contexts: List[ExecutionContext] = []
        self._active_contexts: Dict[str, ExecutionContext] = {}

        # Setup logger
        self.logger = logging.getLogger(f"{__name__}.tracer")
        self.logger.setLevel(log_level)

    def get_context_key(self) -> str:
        """Eindeutiger Kontext-Schlüssel für Thread + Function"""
        return f"{threading.current_thread().ident}_{inspect.stack()[2].function}"

    def start_function(self, function_name: str, **kwargs) -> ExecutionContext:
        """Startet das Tracking einer Funktion"""
        context_key = f"{threading.current_thread().ident}_{function_name}"

        with self._lock:
            # Call Stack erfassen
            call_stack = []
            for frame_info in inspect.stack()[1:6]:  # Top 5 frames
                call_stack.append(f"{frame_info.filename}:{frame_info.lineno} in {frame_info.function}")

            context = ExecutionContext(
                function_name=function_name,
                thread_id=str(threading.current_thread().ident),
                start_time=time.time(),
                arguments=kwargs.copy() if self.include_arguments else {},
                call_stack=call_stack
            )

            # Entry Step
            entry_step = ExecutionStep(
                timestamp=time.time(),
                function_name=function_name,
                step_name="ENTRY",
                step_type="entry",
                details={"arguments": kwargs if self.include_arguments else {}}
            )
            context.steps.append(entry_step)

            self._active_contexts[context_key] = context

            # Log Entry
            self.logger.log(self.log_level,
                f"[TRACE_ENTRY] {function_name}({', '.join(f'{k}={v}' for k, v in (kwargs.items() if self.include_arguments else []))})",
                extra={
                    'event_type': 'TRACE_ENTRY',
                    'function': function_name,
                    'thread_id': context.thread_id,
                    'arguments': kwargs if self.include_arguments else {}
                })

            return context

    def log_step(self, step_name: str, **details):
        """Loggt einen Ausführungsschritt"""
        context_key = None
        function_name = "unknown"

        try:
            # Aktuelle Funktion aus Stack ermitteln
            caller_frame = inspect.stack()[1]
            function_name = caller_frame.function
            context_key = f"{threading.current_thread().ident}_{function_name}"

            with self._lock:
                context = self._active_contexts.get(context_key)
                if not context:
                    # Fallback: Neue temporäre Kontext erstellen
                    context = self.start_function(function_name)

                # Variable State erfassen (optional)
                variables = {}
                if self.include_variables:
                    frame = caller_frame.frame
                    local_vars = frame.f_locals
                    # Nur primitive Typen und wichtige Objekte
                    for var_name, var_value in local_vars.items():
                        if not var_name.startswith('_'):
                            try:
                                if isinstance(var_value, (str, int, float, bool, list, dict)):
                                    if isinstance(var_value, (list, dict)) and len(str(var_value)) > 200:
                                        variables[var_name] = f"<{type(var_value).__name__}[{len(var_value)}]>"
                                    else:
                                        variables[var_name] = var_value
                                elif hasattr(var_value, '__dict__'):
                                    variables[var_name] = f"<{type(var_value).__name__} object>"
                                else:
                                    variables[var_name] = str(type(var_value).__name__)
                            except Exception:
                                # Catch all serialization errors for variable inspection
                                variables[var_name] = "<unserializable>"

                step = ExecutionStep(
                    timestamp=time.time(),
                    function_name=function_name,
                    step_name=step_name,
                    step_type="step",
                    details=details,
                    variables=variables
                )
                context.steps.append(step)

                # Log Step
                self.logger.log(self.log_level,
                    f"[TRACE_STEP] {function_name}.{step_name}: {details}",
                    extra={
                        'event_type': 'TRACE_STEP',
                        'function': function_name,
                        'step': step_name,
                        'details': details,
                        'variables': variables if self.include_variables else {}
                    })

        except Exception as e:
            logger.error(f"Debug tracer step logging failed: {e}")

    def log_error(self, error: Exception, context_info: Dict[str, Any] = None):
        """Loggt einen Fehler mit vollständigem Kontext"""
        try:
            caller_frame = inspect.stack()[1]
            function_name = caller_frame.function
            context_key = f"{threading.current_thread().ident}_{function_name}"

            with self._lock:
                context = self._active_contexts.get(context_key)
                if context:
                    context.error = error

                # Vollständiger Traceback
                tb_lines = traceback.format_exception(type(error), error, error.__traceback__)
                full_traceback = ''.join(tb_lines)

                # Error Step
                error_step = ExecutionStep(
                    timestamp=time.time(),
                    function_name=function_name,
                    step_name="ERROR",
                    step_type="error",
                    details={
                        'error_type': type(error).__name__,
                        'error_message': str(error),
                        'traceback': full_traceback,
                        'context_info': context_info or {}
                    }
                )

                if context:
                    error_step.duration_ms = (error_step.timestamp - context.start_time) * 1000
                    context.steps.append(error_step)

                # Detailliertes Error Log
                self.logger.error(
                    f"[TRACE_ERROR] {function_name} failed after {error_step.duration_ms:.2f}ms: {error}",
                    extra={
                        'event_type': 'TRACE_ERROR',
                        'function': function_name,
                        'error_type': type(error).__name__,
                        'error_message': str(error),
                        'duration_ms': error_step.duration_ms,
                        'context_info': context_info or {},
                        'full_traceback': full_traceback,
                        'execution_steps': [
                            {
                                'step': step.step_name,
                                'type': step.step_type,
                                'timestamp': step.timestamp,
                                'details': step.details
                            } for step in (context.steps if context else [])
                        ]
                    }
                )

        except Exception as trace_error:
            logger.error(f"Debug tracer error logging failed: {trace_error}")

    def end_function(self, result: Any = None, error: Exception = None):
        """Beendet das Tracking einer Funktion"""
        try:
            caller_frame = inspect.stack()[1]
            function_name = caller_frame.function
            context_key = f"{threading.current_thread().ident}_{function_name}"

            with self._lock:
                context = self._active_contexts.get(context_key)
                if not context:
                    return

                context.result = result
                if error:
                    context.error = error

                # Exit Step
                exit_step = ExecutionStep(
                    timestamp=time.time(),
                    function_name=function_name,
                    step_name="EXIT",
                    step_type="exit" if not error else "error",
                    details={
                        'result': str(type(result).__name__) if result is not None else None,
                        'success': error is None
                    },
                    duration_ms=context.duration_ms
                )
                context.steps.append(exit_step)

                # Log Exit
                status = "SUCCESS" if not error else "ERROR"
                self.logger.log(self.log_level,
                    f"[TRACE_EXIT] {function_name} {status} after {context.duration_ms:.2f}ms",
                    extra={
                        'event_type': 'TRACE_EXIT',
                        'function': function_name,
                        'status': status,
                        'duration_ms': context.duration_ms,
                        'result_type': str(type(result).__name__) if result is not None else None,
                        'step_count': len(context.steps)
                    })

                # Context archivieren
                del self._active_contexts[context_key]
                self._contexts.append(context)

                # Cleanup alte Kontexte
                if len(self._contexts) > self.max_contexts:
                    self._contexts = self._contexts[-self.max_contexts:]

        except Exception as e:
            logger.error(f"Debug tracer end function failed: {e}")

    def get_execution_summary(self, function_name: str = None) -> Dict[str, Any]:
        """Liefert Zusammenfassung der Ausführungen"""
        with self._lock:
            contexts = self._contexts
            if function_name:
                contexts = [c for c in contexts if c.function_name == function_name]

            if not contexts:
                return {"message": "No execution data found"}

            total_calls = len(contexts)
            successful_calls = len([c for c in contexts if not c.error])
            failed_calls = total_calls - successful_calls

            avg_duration = sum(c.duration_ms for c in contexts) / total_calls

            return {
                "total_calls": total_calls,
                "successful_calls": successful_calls,
                "failed_calls": failed_calls,
                "success_rate": (successful_calls / total_calls) * 100,
                "average_duration_ms": avg_duration,
                "recent_errors": [
                    {
                        "function": c.function_name,
                        "error": str(c.error),
                        "duration_ms": c.duration_ms,
                        "timestamp": c.start_time
                    } for c in contexts[-10:] if c.error
                ]
            }

# Global Tracer Instance
_global_tracer: Optional[DebugTracer] = None

def get_tracer() -> DebugTracer:
    """Holt oder erstellt den globalen Tracer"""
    global _global_tracer
    if _global_tracer is None:
        _global_tracer = DebugTracer(
            log_level=logging.DEBUG,
            include_variables=True,
            include_arguments=True
        )
    return _global_tracer

# Decorator für automatisches Function Tracing
def trace_function(include_args: bool = True, include_result: bool = True):
    """Decorator für automatisches Funktions-Tracing"""
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            tracer = get_tracer()
            function_name = f"{func.__module__}.{func.__qualname__}"

            # Arguments sammeln
            func_args = {}
            if include_args:
                sig = inspect.signature(func)
                bound_args = sig.bind(*args, **kwargs)
                bound_args.apply_defaults()
                func_args = dict(bound_args.arguments)
                func_args.pop('self', None)
                func_args.pop('cls', None)

                # Große Objekte kürzen
                for key, value in func_args.items():
                    if isinstance(value, (list, dict)) and len(str(value)) > 100:
                        func_args[key] = f"<{type(value).__name__}[{len(value)}]>"
                    elif hasattr(value, '__dict__') and not isinstance(value, (str, int, float, bool)):
                        func_args[key] = f"<{type(value).__name__} object>"

            # Start Tracing
            tracer.start_function(function_name, **func_args)

            try:
                result = func(*args, **kwargs)
                tracer.end_function(result if include_result else None)
                return result
            except Exception as e:
                tracer.log_error(e, {"function_args": func_args})
                tracer.end_function(error=e)
                raise

        return wrapper
    return decorator

# Convenience Functions
def trace_step(step_name: str, **details):
    """Loggt einen Ausführungsschritt"""
    get_tracer().log_step(step_name, **details)

def trace_error(error: Exception, **context):
    """Loggt einen Fehler"""
    get_tracer().log_error(error, context)

def get_execution_summary(function_name: str = None) -> Dict[str, Any]:
    """Holt Ausführungsstatistiken"""
    return get_tracer().get_execution_summary(function_name)
