import asyncio
from contextlib import asynccontextmanager, aclosing

@asynccontextmanager
async def my_semaphore():
    print("Acquiring semaphore")
    try:
        yield
    finally:
        print("Releasing semaphore")

async def inner_gen():
    async with my_semaphore():
        yield 1
        yield 2

async def process_message():
    async with aclosing(inner_gen()) as gen:
        async for i in gen:
            yield i

async def main():
    agent_generator = process_message()
    async for i in agent_generator:
        print("Got:", i)
        break
    
    print(f"ag_running: {agent_generator.ag_running}")
    print(f"ag_frame: {agent_generator.ag_frame}")
    
    try:
        if agent_generator.ag_running: 
            print("aclose path 1")
            await agent_generator.aclose()
        elif not agent_generator.ag_running and agent_generator.ag_frame is not None: 
            print("aclose path 2")
            await agent_generator.aclose() 
    except Exception as e:
        print(e)
    
    await asyncio.sleep(0.5)

asyncio.run(main())
