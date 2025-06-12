#!/usr/bin/env python3
"""
Convenience script to run the Normative Reference Resolver pipeline tests.
Run this from the project root directory.
"""

import subprocess
import sys
from pathlib import Path

def run_pipeline_test():
    """Run the main pipeline test from the testing directory."""
    # Ensure we're in the project root
    project_root = Path(__file__).parent
    test_script_path = project_root / "tests" / "pipeline_testing" / "test_simple_pipeline.py"
    
    if not test_script_path.exists():
        print(f"❌ Test script not found at: {test_script_path}")
        return False
    
    print("🚀 Running Normative Reference Resolver Pipeline Test...")
    print(f"📍 Test script: {test_script_path}")
    print("="*60)
    
    try:
        # Change to testing directory and run the test
        result = subprocess.run(
            [sys.executable, "test_simple_pipeline.py"],
            cwd=test_script_path.parent,
            capture_output=False,  # Show output in real-time
            check=True
        )
        
        print("="*60)
        print("✅ Pipeline test completed successfully!")
        return True
        
    except subprocess.CalledProcessError as e:
        print("="*60)
        print(f"❌ Pipeline test failed with exit code: {e.returncode}")
        return False
    except Exception as e:
        print("="*60)
        print(f"❌ Error running pipeline test: {e}")
        return False

def main():
    """Main entry point."""
    print("🔬 Normative Reference Resolver Pipeline Test Runner")
    print("="*60)
    
    success = run_pipeline_test()
    
    if success:
        print("\n🎯 Check the logs in tests/pipeline_testing/ for detailed results!")
        print("📊 Log files contain comprehensive component traces and metrics")
    else:
        print("\n🔧 Check your environment variables and dependencies")
        print("📋 Ensure .env.local contains MISTRAL_API_KEY and Legifrance credentials")
    
    return 0 if success else 1

if __name__ == "__main__":
    sys.exit(main()) 