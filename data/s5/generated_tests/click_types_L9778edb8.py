import os
import tempfile
import click
from click.types import Path
from click import Context

def test_mutant_kill_executable_or():
    # Create a temporary file that is executable
    with tempfile.NamedTemporaryFile(delete=False) as f:
        temp_path = f.name
    try:
        os.chmod(temp_path, 0o755)  # rwxr-xr-x, executable
        
        # Create a Path type with executable=True
        path_type = Path(exists=True, executable=True)
        
        # Create a proper Click command and context
        @click.command()
        def dummy():
            pass
        
        ctx = Context(dummy)
        # Use a real parameter from the command
        param = dummy.params[0] if dummy.params else None
        if param is None:
            # Create a simple option parameter
            @click.command()
            @click.option('--test', type=click.Path(exists=True, executable=True))
            def dummy2(test):
                pass
            ctx = Context(dummy2)
            param = dummy2.params[0]
        
        # Original: executable=True and not os.access(value, os.X_OK) -> False (since accessible) -> no fail
        # Mutant: executable=True or not os.access(...) -> True (because executable=True) -> fail
        result = path_type.convert(temp_path, param, ctx)
        assert result == temp_path  # Should pass for original, fail for mutant
    finally:
        os.unlink(temp_path)
