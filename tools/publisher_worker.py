"""One account per process. Attaches to existing browser; never owns its lifetime."""
import argparse
from control_console.store import ConsoleStore
from control_console.publisher_execution import PublishExecutionService

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--runtime',required=True)
    parser.add_argument('--run-id',required=True)
    parser.add_argument('--account-id',required=True)
    args=parser.parse_args()
    service=PublishExecutionService(ConsoleStore(args.runtime),None,launch_workers=False)
    service.run_account(args.run_id,args.account_id)

if __name__=='__main__':main()
