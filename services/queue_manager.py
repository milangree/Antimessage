import asyncio
from config import config

class MessageQueue:
    def __init__(self):
        self.queue = asyncio.Queue()
    
    async def add_message(self, message_data):
        await self.queue.put(message_data)
    
    async def worker(self, name):
        while True:
            try:
                message_data = await self.queue.get()
                
                update = message_data['update']
                context = message_data['context']
                user = update.effective_user
                
                
                from services.gemini_service import gemini_service
                if update.message.text:
                    analysis = await gemini_service.analyze_message(update.message.text)
                    if analysis['is_spam']:
                        await context.bot.send_message(
                            chat_id=user.id,
                            text=f"您的消息被拦截，原因：{analysis['reason']}"
                        )
                        self.queue.task_done()
                        continue
                
                
                from services.thread_manager import get_or_create_thread
                thread_id = await get_or_create_thread(update, context)
                
                if thread_id:
                    await context.bot.forward_message(
                        chat_id=config.FORUM_GROUP_ID,
                        from_chat_id=user.id,
                        message_id=update.message.message_id,
                        message_thread_id=thread_id
                    )
                
                self.queue.task_done()
            except Exception as e:
                print(f"Worker {name} 错误: {e}")
    
    async def start(self):
        tasks = []
        for i in range(config.MAX_WORKERS):
            task = asyncio.create_task(self.worker(f"Worker-{i+1}"))
            tasks.append(task)
        
        await self.queue.join()
        
        for task in tasks:
            task.cancel()
        
        await asyncio.gather(*tasks, return_exceptions=True)


message_queue = MessageQueue()