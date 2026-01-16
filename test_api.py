# -*- coding: utf-8 -*-
import os
import google.generativeai as genai
from dotenv import load_dotenv

# 1. ?섍꼍蹂??濡쒕뱶
load_dotenv()
api_key = os.getenv("NANOBANANA_API_KEY_2")

print(f"?뵎 濡쒕뱶??API ?? {api_key[:10]}... (?ㅻ뒗 ?앸왂)")

if not api_key or "AIzaSyCbbvdem" in api_key:
    print("??[寃쎄퀬] API ?ㅺ? ?덉떆??Placeholder)?닿굅??鍮꾩뼱?덉뒿?덈떎!")
    print("   -> .env ?뚯씪??蹂몄씤???ㅼ젣 Google API ?ㅻ? ?ｌ뼱二쇱꽭??")
    exit()

# 2. 紐⑤뜽 ?곌껐 ?뚯뒪??
genai.configure(api_key=api_key)
model_name = 'gemini-3-pro-image' # ?뱀? 'gemini-2.0-flash'

print(f"?쨼 紐⑤뜽 ?곌껐 ?뚯뒪??以?({model_name})...")

try:
    model = genai.GenerativeModel(model_name)
    response = model.generate_content("Hello! Are you working?")
    
    if response.text:
        print(f"???깃났! 紐⑤뜽 ?묐떟: {response.text}")
    else:
        print("?좑툘 ?묐떟? ?붿?留??띿뒪?멸? ?놁뒿?덈떎.")
        
except Exception as e:
    print(f"???곌껐 ?ㅽ뙣! ?먮윭 濡쒓렇瑜??뺤씤?섏꽭??\n{e}")
