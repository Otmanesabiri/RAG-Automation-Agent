#!/usr/bin/env python3
"""Test script for Gemini LLM integration."""

import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

def test_gemini_direct():
    """Test direct Gemini API call."""
    print("🧪 Testing direct Gemini API...")
    
    try:
        from google import genai
        
        # Check API key
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            print("❌ GEMINI_API_KEY not set in .env file")
            return False
        
        print(f"✓ API key found: {api_key[:10]}...")
        
        # Create client
        client = genai.Client()
        
        # Test generation
        response = client.models.generate_content(
            model="gemini-2.0-flash-exp",
            contents="Explain AI in one sentence."
        )
        
        print(f"✅ Direct API test successful!")
        print(f"Response: {response.text}")
        return True
        
    except Exception as e:
        print(f"❌ Direct API test failed: {e}")
        return False


def test_gemini_provider():
    """Test Gemini through LLMProvider abstraction."""
    print("\n🧪 Testing Gemini via LLMProvider...")
    
    try:
        from src.llm.service import GeminiLLMProvider
        
        # Create provider
        provider = GeminiLLMProvider(model="gemini-2.0-flash-exp")
        print("✓ Provider created")
        
        # Test generation
        response = provider.generate("What is machine learning? Answer in one sentence.")
        print(f"✅ Provider test successful!")
        print(f"Response: {response}")
        
        # Test streaming
        print("\n🧪 Testing streaming...")
        print("Response (streamed): ", end="")
        for chunk in provider.stream_generate("Count from 1 to 5."):
            print(chunk, end="", flush=True)
        print("\n✅ Streaming test successful!")
        
        return True
        
    except Exception as e:
        print(f"❌ Provider test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_llm_service():
    """Test LLMService with Gemini provider."""
    print("\n🧪 Testing LLMService with GEMINI provider...")
    
    try:
        # Set provider
        os.environ["LLM_PROVIDER"] = "gemini"
        
        from src.llm.service import LLMService
        
        # Create service
        service = LLMService()
        print("✓ Service created with Gemini provider")
        
        # Test generation
        response = service.generate("What is deep learning? Answer briefly.")
        print(f"✅ Service test successful!")
        print(f"Response: {response}")
        
        return True
        
    except Exception as e:
        print(f"❌ Service test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """Run all tests."""
    print("=" * 60)
    print("🚀 Gemini LLM Integration Test Suite")
    print("=" * 60)
    
    results = []
    
    # Test 1: Direct API
    results.append(("Direct API", test_gemini_direct()))
    
    # Test 2: Provider abstraction
    results.append(("Provider", test_gemini_provider()))
    
    # Test 3: LLMService
    results.append(("LLMService", test_llm_service()))
    
    # Summary
    print("\n" + "=" * 60)
    print("📊 Test Summary")
    print("=" * 60)
    
    for name, passed in results:
        status = "✅ PASSED" if passed else "❌ FAILED"
        print(f"{name}: {status}")
    
    total_passed = sum(1 for _, passed in results if passed)
    print(f"\nTotal: {total_passed}/{len(results)} tests passed")
    
    return all(passed for _, passed in results)


if __name__ == "__main__":
    success = main()
    exit(0 if success else 1)
